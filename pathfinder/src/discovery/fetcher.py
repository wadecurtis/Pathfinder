"""Smart job description fetcher with multiple fallback strategies.

Strategy 1: requests + BeautifulSoup (fast, works for server-rendered pages)
Strategy 2: Playwright headless browser (handles JS-rendered content)
Strategy 3: Playwright screenshot + LLM vision extraction (last resort)
"""

import base64
import logging
import re

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Minimum characters for a valid job description
MIN_DESC_LENGTH = 200


def fetch_description(url: str) -> dict:
    """Fetch job description from a URL using progressively heavier strategies.

    Returns dict with:
        - description: str (the extracted text)
        - method: str (how it was fetched: "requests", "playwright", "screenshot+llm", "failed")
        - error: str | None
    """
    if not url:
        return {"description": "", "method": "failed", "error": "No URL provided"}

    # Strategy 1: requests + BeautifulSoup
    desc = _fetch_with_requests(url)
    if desc and len(desc) >= MIN_DESC_LENGTH:
        logger.info(f"Fetched description via requests ({len(desc)} chars)")
        return {"description": desc, "method": "requests", "error": None}

    # Strategy 2: Playwright headless browser
    desc = _fetch_with_playwright(url)
    if desc and len(desc) >= MIN_DESC_LENGTH:
        logger.info(f"Fetched description via playwright ({len(desc)} chars)")
        return {"description": desc, "method": "playwright", "error": None}

    # Strategy 3: Screenshot + LLM vision
    desc = _fetch_with_screenshot_llm(url)
    if desc and len(desc) >= MIN_DESC_LENGTH:
        logger.info(f"Fetched description via screenshot+LLM ({len(desc)} chars)")
        return {"description": desc, "method": "screenshot+llm", "error": None}

    return {
        "description": desc or "",
        "method": "failed",
        "error": "Could not extract a meaningful job description from the page. You can paste it manually.",
    }


def _extract_description(soup: BeautifulSoup) -> str:
    """Extract job description text from a parsed page using common patterns."""
    # Try specific job description containers (ordered by specificity)
    selectors = [
        # LinkedIn
        ("div", {"class": re.compile(r"description|show-more")}),
        # Indeed
        ("div", {"id": "jobDescriptionText"}),
        # Greenhouse
        ("div", {"class": re.compile(r"job__description")}),
        ("div", {"class": re.compile(r"job-post-container")}),
        # Lever
        ("div", {"class": re.compile(r"posting-page")}),
        ("div", {"class": re.compile(r"section-wrapper")}),
        # Workday
        ("div", {"data-automation-id": re.compile(r"jobPosting")}),
        # Generic ATS
        ("div", {"class": re.compile(r"job.?description", re.I)}),
        ("section", {"class": re.compile(r"job.?description", re.I)}),
        ("div", {"id": re.compile(r"job.?description", re.I)}),
        ("article", {"class": re.compile(r"job|posting", re.I)}),
        # Broad fallbacks
        ("div", {"class": re.compile(r"description")}),
        ("main", {}),
        ("article", {}),
        ("div", {"role": "main"}),
        ("div", {"id": "content"}),
        ("div", {"class": re.compile(r"content")}),
    ]

    for tag, attrs in selectors:
        element = soup.find(tag, attrs) if attrs else soup.find(tag)
        if element:
            text = element.get_text(separator="\n", strip=True)
            if len(text) >= MIN_DESC_LENGTH:
                return text[:8000]

    # Last resort: full body text
    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        if len(text) >= MIN_DESC_LENGTH:
            return text[:8000]

    return ""


def _fetch_with_requests(url: str) -> str:
    """Strategy 1: Simple HTTP request + BeautifulSoup parsing."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove script/style tags
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        return _extract_description(soup)
    except Exception as e:
        logger.debug(f"requests fetch failed for {url}: {e}")
        return ""


def _fetch_with_playwright(url: str) -> str:
    """Strategy 2: Headless browser to handle JS-rendered content."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

            page.goto(url, wait_until="networkidle", timeout=30000)

            # Wait a bit for lazy-loaded content
            page.wait_for_timeout(2000)

            # Try clicking "show more" buttons (common on LinkedIn, Indeed)
            for selector in [
                "button:has-text('Show more')",
                "button:has-text('show more')",
                "button.show-more",
                "[class*='show-more']",
                "button:has-text('See full description')",
                "button:has-text('Read more')",
            ]:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        return _extract_description(soup)
    except Exception as e:
        logger.debug(f"Playwright fetch failed for {url}: {e}")
        return ""


def _fetch_with_screenshot_llm(url: str) -> str:
    """Strategy 3: Take full-page screenshots and use LLM to extract the job description."""
    try:
        from playwright.sync_api import sync_playwright

        screenshots = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)

            # Click show more if available
            for selector in [
                "button:has-text('Show more')",
                "button.show-more",
                "[class*='show-more']",
            ]:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            # Take up to 3 screenshots scrolling down the page
            total_height = page.evaluate("document.body.scrollHeight")
            viewport_height = 900
            num_screenshots = min(3, max(1, total_height // viewport_height + 1))

            for i in range(num_screenshots):
                scroll_y = i * viewport_height
                page.evaluate(f"window.scrollTo(0, {scroll_y})")
                page.wait_for_timeout(500)
                screenshot_bytes = page.screenshot(type="png")
                screenshots.append(base64.b64encode(screenshot_bytes).decode())

            browser.close()

        if not screenshots:
            return ""

        # Use LLM to extract job description from screenshots
        return _extract_from_screenshots(screenshots)
    except Exception as e:
        logger.warning(f"Screenshot+LLM fetch failed for {url}: {e}")
        return ""


def _extract_from_screenshots(screenshots_b64: list[str]) -> str:
    """Use LLM vision to extract job description from page screenshots."""
    try:
        from ..llm_client import get_llm_response

        # Build a prompt with screenshots described
        prompt = (
            "I've taken screenshots of a job posting page. "
            "Please extract the complete job description text from these screenshots. "
            "Include: job title, company, location, responsibilities, requirements, "
            "qualifications, and any other relevant details. "
            "Return ONLY the extracted job description text, no commentary.\n\n"
            "Screenshots are attached as base64 PNG images.\n"
        )

        # For Groq (which may not support vision), fall back to a text-only approach
        # Try sending as a vision request first
        try:
            from ..profile_loader import load_settings
            settings = load_settings()
            llm_config = settings.get("llm", {})
            provider = llm_config.get("provider", "groq")

            if provider == "anthropic":
                # Anthropic supports vision natively
                import anthropic
                client = anthropic.Anthropic(api_key=llm_config.get("api_key"))
                content = [{"type": "text", "text": prompt}]
                for img_b64 in screenshots_b64:
                    content.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    })
                response = client.messages.create(
                    model=llm_config.get("model", "claude-sonnet-4-20250514"),
                    max_tokens=4000,
                    messages=[{"role": "user", "content": content}],
                )
                return response.content[0].text
            else:
                # Groq doesn't support vision — use OCR-like text extraction from playwright instead
                logger.info("LLM provider doesn't support vision, skipping screenshot extraction")
                return ""
        except Exception as e:
            logger.debug(f"Vision extraction failed: {e}")
            return ""

    except Exception as e:
        logger.warning(f"Screenshot LLM extraction failed: {e}")
        return ""
