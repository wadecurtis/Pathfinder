"""
Ghost job detection — checks posting freshness, repost history, and career page presence.

Returns one of five states for any QUALIFY or NEUTRAL job:
  - "Verified"     — all signals positive: career page found, fresh (<60d), no repost
  - "clean"        — inconclusive; not enough signal to confirm or deny (no badge shown)
  - "Low Risk"     — weak signal only (posting age 60+ days)
  - "Unverified"   — moderate signal (repost history found)
  - "Ghost Likely" — strong signal (role absent from company careers page,
                     or multiple signals combining to high confidence)

Signal weights (applied in priority order):
  1. Career page present/absent — strongest  → Verified or Ghost Likely alone
  2. Repost history             — moderate   → Unverified; Ghost Likely if combined with age
  3. Posting age 60+ d          — weak       → Low Risk alone
"""

import logging
import re
import sqlite3
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Imported lazily inside functions to avoid import-time circular issues
# (tracker imports nothing from ghost_detector, so a top-level import is safe,
# but deferred here so tests can mock the module boundary cleanly).
def _tracker():
    from . import tracker
    return tracker

# Aggregate job boards / ATS platforms — results from these do NOT count as
# the role appearing on the company's own careers page.
_JOB_BOARDS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "monster.com",
    "ziprecruiter.com", "simplyhired.com", "careerbuilder.com",
    "workday.com", "greenhouse.io", "lever.co", "jobvite.com",
    "taleo.net", "smartrecruiters.com", "icims.com", "bamboohr.com",
    "wellfound.com", "builtinnyc.com", "builtin.com", "jobs.com",
    "snagajob.com", "dice.com", "recruit.net", "jooble.org",
    "adzuna.com", "jobillico.com", "eluta.ca", "workopolis.com",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%m/%d/%Y",
]


# ── Signal helpers ────────────────────────────────────────────────────────────

def _get_posting_age(date_posted: str) -> int | None:
    """Return days since the posting date, or None if the date is unparseable."""
    if not date_posted:
        return None
    raw = date_posted.strip()
    for fmt in _DATE_FORMATS:
        try:
            # Truncate to the length the format expects so strptime doesn't choke
            # on trailing timezone or millisecond suffixes.
            dt = datetime.strptime(raw[: len(fmt) + 4], fmt)
            return max(0, (datetime.now() - dt).days)
        except (ValueError, TypeError):
            continue
    return None


def _normalize_title(title: str) -> set[str]:
    """Lower-case word set, stripped of punctuation, for fuzzy title comparison."""
    stop = {"and", "the", "a", "an", "of", "in", "for", "at", "to", "with", "or"}
    words = re.sub(r"[^\w\s]", " ", title.lower()).split()
    return {w for w in words if len(w) > 2 and w not in stop}


def _check_repost_history(company: str, title: str, current_date_posted: str) -> bool:
    """
    Return True if the job_cache already contains a posting from the same company
    with a similar title and an earlier date — indicating the role was previously listed.
    """
    import os
    db_path = os.path.join(os.path.dirname(__file__), "..", "data", "tracker.db")

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        company_lower = company.strip().lower()
        rows = conn.execute(
            """SELECT title, date_posted FROM job_cache
               WHERE lower(company) = ? AND date_posted != ''""",
            (company_lower,),
        ).fetchall()
        conn.close()

        if not rows:
            return False

        current_words = _normalize_title(title)
        if not current_words:
            return False

        for row in rows:
            cached_words = _normalize_title(row["title"])
            if not cached_words:
                continue
            overlap = current_words & cached_words
            similarity = len(overlap) / len(current_words)
            if similarity >= 0.6:
                # Similar title found — now confirm the cached posting pre-dates this one.
                cached_date = row["date_posted"] or ""
                if current_date_posted and cached_date:
                    # Both have dates: only flag as repost if cached one is earlier.
                    if cached_date < current_date_posted:
                        return True
                else:
                    # One or both dates missing — any prior cache hit counts.
                    return True

        return False

    except Exception as exc:
        logger.debug(f"Repost history check failed for {company}: {exc}")
        return False


def _fetch_career_page(company: str, title: str) -> bool | None:
    """
    Hit DuckDuckGo and return whether the role appears on the company's own
    careers page (True), is absent (False), or is indeterminate (None).
    This is the live-fetch path — call _check_career_page() instead, which
    gates this behind the 7-day TTL cache.
    """
    query = f'"{company}" "{title}"'
    url = "https://html.duckduckgo.com/html/"

    try:
        resp = requests.get(
            url,
            params={"q": query},
            headers=_HEADERS,
            timeout=8,
        )
        if resp.status_code != 200:
            logger.debug(f"DuckDuckGo returned {resp.status_code} for {company}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")

        # DuckDuckGo HTML results — result URLs live in .result__url or
        # as href on .result__a anchors.
        result_links = []
        for tag in soup.select(".result__url"):
            text = tag.get_text(strip=True).lower()
            if text:
                result_links.append(text)
        if not result_links:
            for tag in soup.select(".result__a"):
                href = (tag.get("href") or "").lower()
                if href:
                    result_links.append(href)

        if not result_links:
            # No results at all — could mean the search returned nothing or
            # the HTML structure changed; treat as inconclusive.
            return None

        # Build a slug from the company name to fuzzy-match against domains.
        company_slug = re.sub(r"[^\w]", "", company.lower())
        # Use the first 6+ chars for matching (handles "Salesforce" → "salesfo").
        slug_probe = company_slug[:max(6, len(company_slug) // 2)]

        for link in result_links[:8]:
            is_board = any(board in link for board in _JOB_BOARDS)
            if not is_board and slug_probe in link:
                logger.debug(f"Career page hit for {company}: {link}")
                return True

        return False

    except requests.exceptions.Timeout:
        logger.debug(f"Career page check timed out for {company}")
        return None
    except Exception as exc:
        logger.debug(f"Career page check error for {company}: {exc}")
        return None


def _check_career_page(company: str, title: str) -> bool | None:
    """
    Return the career page presence result, using a 7-day TTL cache.

    Cache hit (fresh)  → return stored result, skip network call.
    Cache miss / stale → run live fetch, store result, return it.

    Returns:
        True  — role found on a non-aggregator domain matching the company
        False — search succeeded but no company-site results found
        None  — search could not be completed (network error, blocked, etc.)
    """
    t = _tracker()

    cached_result, is_fresh = t.get_career_page_cache(company, title)
    if is_fresh:
        logger.debug(f"Career page cache hit (fresh) for {company}: {cached_result}")
        return cached_result

    if cached_result is not None:
        logger.debug(f"Career page cache stale for {company} — re-fetching")

    result = _fetch_career_page(company, title)

    # Only persist definitive results (True/False); don't cache inconclusive
    # None so the next run still tries a live fetch.
    if result is not None:
        t.set_career_page_cache(company, title, result)

    return result


# ── Main detection function ───────────────────────────────────────────────────

def detect_ghost(job: dict) -> str:
    """
    Run all three detection signals and return a ghost state string.

    Args:
        job: scored job dict — must have keys: company, title, date_posted

    Returns:
        One of: "Verified", "clean", "Low Risk", "Unverified", "Ghost Likely"
    """
    company     = job.get("company", "")
    title       = job.get("title", "")
    date_posted = job.get("date_posted", "")

    if not company or not title:
        return "clean"

    # ── Human override (email reply feedback) ────────────────────────────────
    # If a reply correction exists within the 90-day TTL, trust it over all
    # automated signals and skip the rest of the pipeline entirely.
    override = _tracker().get_active_ghost_override(company)
    if override == "confirmed_real":
        logger.debug(f"Ghost override (confirmed_real) applied for {company!r}")
        return "Verified"
    if override == "confirmed_ghost":
        logger.debug(f"Ghost override (confirmed_ghost) applied for {company!r}")
        return "Ghost Likely"

    # ── Signal 1: Posting age ─────────────────────────────────────────────────
    age_days = _get_posting_age(date_posted)
    stale = age_days is not None and age_days >= 60

    # ── Signal 2: Repost history ──────────────────────────────────────────────
    repost = _check_repost_history(company, title, date_posted)

    # ── Signal 3: Career page presence ───────────────────────────────────────
    career_page = _check_career_page(company, title)
    # True  = found on company site   → strong positive signal
    # False = not found on company site → strong negative signal
    # None  = couldn't determine      → inconclusive, no signal

    logger.debug(
        f"Ghost signals — {company}: {title}  "
        f"age={age_days}d stale={stale}  repost={repost}  career_page={career_page}"
    )

    # ── Weighting / decision ──────────────────────────────────────────────────
    # All three signals clearly positive → confirmed live role.
    if career_page is True and not stale and not repost:
        return "Verified"

    # Career page explicitly absent is the single strongest negative signal.
    if career_page is False:
        return "Ghost Likely"

    # Repost history + stale age together is high-confidence ghost.
    if repost and stale:
        return "Ghost Likely"

    # Repost alone is a moderate signal.
    if repost:
        return "Unverified"

    # Stale age alone is a weak signal.
    if stale:
        return "Low Risk"

    # No definitive signal in either direction — don't show a badge.
    return "clean"
