"""Parse a single job URL into structured job data."""

import hashlib
import re

import requests
from bs4 import BeautifulSoup

from ..models import JobListing


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def parse_job_url(url: str) -> JobListing:
    """Fetch and parse a job posting URL into a JobListing."""
    if "linkedin.com" in url:
        return _parse_linkedin(url)
    elif "indeed.com" in url:
        return _parse_indeed(url)
    elif "greenhouse.io" in url or "lever.co" in url:
        return _parse_ats_page(url)
    else:
        return _parse_generic(url)


def _parse_linkedin(url: str) -> JobListing:
    """Parse a LinkedIn job posting."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    title = ""
    title_tag = soup.find("h1") or soup.find("h2", class_=re.compile("title|job"))
    if title_tag:
        title = title_tag.get_text(strip=True)

    company = ""
    company_tag = soup.find("a", class_=re.compile("company|org")) or soup.find(
        "span", class_=re.compile("company|org")
    )
    if company_tag:
        company = company_tag.get_text(strip=True)

    description = ""
    desc_div = soup.find("div", class_=re.compile("description|show-more"))
    if desc_div:
        description = desc_div.get_text(separator="\n", strip=True)
    else:
        # Fallback: get all text from the page body
        body = soup.find("body")
        if body:
            description = body.get_text(separator="\n", strip=True)[:5000]

    location = ""
    loc_tag = soup.find("span", class_=re.compile("location|topcard"))
    if loc_tag:
        location = loc_tag.get_text(strip=True)

    job_id = hashlib.md5(url.encode()).hexdigest()[:12]

    return JobListing(
        id=job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        description=description,
        source="linkedin",
    )


def _parse_indeed(url: str) -> JobListing:
    """Parse an Indeed job posting."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    title = ""
    title_tag = soup.find("h1", class_=re.compile("jobTitle|title"))
    if title_tag:
        title = title_tag.get_text(strip=True)

    company = ""
    company_tag = soup.find(attrs={"data-company-name": True}) or soup.find(
        "div", class_=re.compile("company")
    )
    if company_tag:
        company = company_tag.get_text(strip=True)

    description = ""
    desc_div = soup.find("div", id="jobDescriptionText") or soup.find(
        "div", class_=re.compile("description")
    )
    if desc_div:
        description = desc_div.get_text(separator="\n", strip=True)

    job_id = hashlib.md5(url.encode()).hexdigest()[:12]

    return JobListing(
        id=job_id,
        title=title,
        company=company,
        url=url,
        description=description,
        source="indeed",
    )


def _parse_ats_page(url: str) -> JobListing:
    """Parse Greenhouse or Lever job pages."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    title = ""
    title_tag = soup.find("h1")
    if title_tag:
        title = title_tag.get_text(strip=True)

    company = ""
    # Try multiple places for company name
    meta_company = soup.find("meta", property="og:site_name")
    if meta_company:
        company = meta_company.get("content", "")
    if not company:
        # Greenhouse: company name often in the URL path or page title
        company_tag = soup.find("span", class_=re.compile("company"))
        if company_tag:
            company = company_tag.get_text(strip=True)
    if not company and "greenhouse.io/" in url:
        # Extract from URL: greenhouse.io/companyname/jobs/...
        parts = url.split("greenhouse.io/")[-1].split("/")
        if parts:
            company = parts[0].replace("-", " ").title()

    description = ""
    # Try Greenhouse-specific selectors first, then generic ones
    for selector in [
        ("div", {"class": re.compile(r"job__description")}),
        ("div", {"class": re.compile(r"job-post-container")}),
        ("div", {"class": re.compile(r"posting-page")}),
        ("div", {"id": "content"}),
        ("section", {"class": re.compile(r"job")}),
        ("div", {"class": re.compile(r"content")}),
    ]:
        content_div = soup.find(selector[0], selector[1])
        if content_div and len(content_div.get_text(strip=True)) > 100:
            description = content_div.get_text(separator="\n", strip=True)
            break

    # Fallback: get body text
    if not description:
        body = soup.find("body")
        if body:
            description = body.get_text(separator="\n", strip=True)[:5000]

    location = ""
    loc_tag = soup.find("div", class_=re.compile("location"))
    if loc_tag:
        location = loc_tag.get_text(strip=True)

    job_id = hashlib.md5(url.encode()).hexdigest()[:12]
    source = "greenhouse" if "greenhouse" in url else "lever"

    return JobListing(
        id=job_id,
        title=title,
        company=company,
        location=location,
        url=url,
        description=description,
        source=source,
    )


def _parse_generic(url: str) -> JobListing:
    """Generic fallback parser for any job URL."""
    resp = requests.get(url, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    title = ""
    for tag in ["h1", "h2"]:
        found = soup.find(tag)
        if found:
            title = found.get_text(strip=True)
            break

    # Try to get the page title as fallback
    if not title:
        page_title = soup.find("title")
        if page_title:
            title = page_title.get_text(strip=True)

    description = ""
    # Try common containers
    for selector in [
        "div.job-description",
        "div.description",
        "article",
        "main",
        "div.content",
    ]:
        desc_tag = soup.select_one(selector)
        if desc_tag:
            description = desc_tag.get_text(separator="\n", strip=True)
            break

    if not description:
        body = soup.find("body")
        if body:
            description = body.get_text(separator="\n", strip=True)[:5000]

    job_id = hashlib.md5(url.encode()).hexdigest()[:12]

    return JobListing(
        id=job_id,
        title=title,
        company="",
        url=url,
        description=description,
        source="web",
    )
