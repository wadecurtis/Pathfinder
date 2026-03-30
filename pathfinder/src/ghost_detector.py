"""
Ghost job detection — checks posting freshness and repost history.

Returns one of four states for any QUALIFY or NEUTRAL job:
  - "clean"        — inconclusive; not enough signal to confirm or deny (no badge shown)
  - "Low Risk"     — weak signal only (posting age 60+ days)
  - "Unverified"   — moderate signal (repost history found)
  - "Ghost Likely" — strong signal (repost history + stale age together)

Career page discovery is handled separately by find_careers_page_url(), which
returns a URL for the candidate to use — it is not a detection signal.
"""

import logging
import re
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

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

from ._http import HEADERS as _HEADERS

_CORP_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|group|solutions|services|"
    r"consulting|technologies|tech|systems|global|international|staffing|"
    r"partners?)\b\.?",
    re.IGNORECASE,
)

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


def _get_company_domain(company: str, job_url: str = "") -> str:
    """
    Derive the most likely company domain.

    Tries the job URL first (if it isn't a known job board), then falls back to
    slugifying the company name and guessing a .com domain.
    Returns a bare domain like 'plative.com', or '' if none can be derived.
    """
    if job_url:
        try:
            netloc = urlparse(job_url).netloc.lower().removeprefix("www.")
            if netloc and not any(board in netloc for board in _JOB_BOARDS):
                return netloc
        except Exception:
            pass

    slug = _CORP_SUFFIXES.sub("", company).strip()
    slug = re.sub(r"[^\w\s]", "", slug).strip()
    slug = re.sub(r"\s+", "", slug).lower()
    return f"{slug}.com" if slug else ""


def find_careers_page_url(company: str, job_url: str = "") -> str | None:
    """
    Return the URL of the company's careers page, or None if not found.

    Checks the 7-day cache first. On a cache miss, probes standard paths in
    order and caches the result (including None, so repeated calls for the same
    company don't trigger fresh HTTP probes within the TTL window).

    The entire probe is wrapped in a hard 12-second wall-clock timeout to
    prevent DNS resolution hangs from stalling the run.

    Args:
        company: Company name - used to derive the domain when job_url is a
                 job board link (e.g. LinkedIn).
        job_url: The job posting URL. Used as the domain source if it's not
                 a known job board.

    Returns:
        URL string (e.g. 'https://plative.com/careers') or None.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    t = _tracker()

    cached_url, is_fresh = t.get_career_page_cache(company)
    if is_fresh:
        logger.debug(f"Careers page cache hit for {company!r}: {cached_url}")
        return cached_url

    def _probe() -> str | None:
        domain = _get_company_domain(company, job_url)
        if not domain:
            logger.debug(f"Could not derive domain for {company!r}")
            return None

        logger.debug(f"Careers page lookup - company={company!r} domain={domain!r}")

        candidates = [
            f"https://{domain}/careers",
            f"https://{domain}/jobs",
            f"https://{domain}/careers/open-roles",
            f"https://{domain}/about/careers",
            f"https://jobs.{domain}",
        ]

        for url in candidates:
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=4, allow_redirects=True)
                if resp.status_code == 200:
                    logger.debug(f"Careers page found: {url}")
                    return url
            except Exception as exc:
                logger.debug(f"Careers page probe error for {url}: {exc}")
        return None

    found_url = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_probe)
            found_url = future.result(timeout=12)
    except FuturesTimeoutError:
        logger.debug(f"Careers page lookup timed out for {company!r}")
    except Exception as exc:
        logger.debug(f"Careers page lookup failed for {company!r}: {exc}")

    t.set_career_page_cache(company, found_url)
    return found_url


# ── Main detection function ───────────────────────────────────────────────────

def detect_ghost(job: dict) -> str:
    """
    Check posting age and repost history and return a ghost state string.

    Args:
        job: scored job dict — must have keys: company, title, date_posted

    Returns:
        One of: "clean", "Low Risk", "Unverified", "Ghost Likely"
    """
    company     = job.get("company", "")
    title       = job.get("title", "")
    date_posted = job.get("date_posted", "")

    if not company or not title:
        return "clean"

    # ── Human override (email reply feedback) ────────────────────────────────
    override = _tracker().get_active_ghost_override(company)
    if override == "confirmed_ghost":
        logger.debug(f"Ghost override (confirmed_ghost) applied for {company!r}")
        return "Ghost Likely"

    # ── Signal 1: Posting age ─────────────────────────────────────────────────
    age_days = _get_posting_age(date_posted)
    stale = age_days is not None and age_days >= 60

    # ── Signal 2: Repost history ──────────────────────────────────────────────
    repost = _check_repost_history(company, title, date_posted)

    # ── Weighting / decision ──────────────────────────────────────────────────
    if repost and stale:
        return "Ghost Likely"

    if repost:
        return "Unverified"

    if stale:
        return "Low Risk"

    return "clean"
