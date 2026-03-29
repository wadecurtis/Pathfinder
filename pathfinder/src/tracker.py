"""Application tracker — seen-jobs dedup and job cache (SQLite)."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tracker.db")

_CAREER_CACHE_TTL_DAYS = 7
_REPOST_CAP_PER_COMPANY = 10
_COMPANY_INACTIVITY_DAYS = 90


def _ensure_data_dir():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def _init_db():
    """Create tables if they don't exist."""
    _ensure_data_dir()
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_jobs (
                job_id TEXT PRIMARY KEY,
                added_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_cache (
                id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                company TEXT DEFAULT '',
                location TEXT DEFAULT '',
                url TEXT DEFAULT '',
                description TEXT DEFAULT '',
                date_posted TEXT DEFAULT '',
                source TEXT DEFAULT '',
                salary TEXT DEFAULT '',
                cached_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migrate from old schema (keyed on company+title, stored boolean) if present
        try:
            conn.execute("SELECT url FROM career_page_cache LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("DROP TABLE IF EXISTS career_page_cache")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS career_page_cache (
                company   TEXT PRIMARY KEY,
                url       TEXT,
                cached_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                company         TEXT PRIMARY KEY,
                ghost_override  TEXT,
                override_set_at TEXT,
                override_source TEXT DEFAULT 'email_reply'
            )
        """)


@contextmanager
def _get_conn():
    """Yield a SQLite connection with WAL mode for better concurrency."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Initialize DB on module import
_init_db()


# ── Seen Jobs (scout dedup) ───────────────────────────────────────────────────

def load_seen_job_ids() -> set:
    with _get_conn() as conn:
        rows = conn.execute("SELECT job_id FROM seen_jobs").fetchall()
        return {r["job_id"] for r in rows}


def save_seen_job_ids(ids: set):
    with _get_conn() as conn:
        for job_id in ids:
            conn.execute(
                "INSERT OR IGNORE INTO seen_jobs (job_id) VALUES (?)", (job_id,)
            )


# ── Job Cache (scraper) ───────────────────────────────────────────────────────

def save_job_to_cache(job_data: dict):
    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO job_cache
               (id, title, company, location, url, description, date_posted, source, salary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_data["id"], job_data.get("title", ""), job_data.get("company", ""),
                job_data.get("location", ""), job_data.get("url", ""),
                job_data.get("description", ""), job_data.get("date_posted", ""),
                job_data.get("source", ""), job_data.get("salary", ""),
            ),
        )


# ── Career Page Cache (ghost detector) ───────────────────────────────────────

def get_career_page_cache(company: str) -> tuple[str | None, bool]:
    """
    Look up a cached careers page URL for a company.

    Returns (url, is_fresh) where:
      url      — careers page URL string, or None if previously checked and not found
      is_fresh — True if the entry is within the 7-day TTL

    Returns (None, False) when no cache entry exists.
    """
    company_key = company.strip().lower()

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT url, cached_at FROM career_page_cache WHERE company = ?",
            (company_key,),
        ).fetchone()

    if row is None:
        return None, False

    try:
        cached_dt = datetime.fromisoformat(row["cached_at"])
        is_fresh  = (datetime.now() - cached_dt).days < _CAREER_CACHE_TTL_DAYS
    except (ValueError, TypeError):
        is_fresh = False

    return row["url"], is_fresh


def set_career_page_cache(company: str, url: str | None):
    """Store or refresh the careers page URL for a company (None = checked, not found)."""
    company_key = company.strip().lower()

    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO career_page_cache
               (company, url, cached_at)
               VALUES (?, ?, datetime('now'))""",
            (company_key, url),
        )


# ── Ghost Overrides (companies table) ────────────────────────────────────────

def get_active_ghost_override(company: str, ttl_days: int = 90) -> str | None:
    """
    Return a ghost override value for the company if one exists within ttl_days,
    or None if no active override is on record.

    Return values: 'confirmed_real' | 'confirmed_ghost' | None
    """
    company_key = company.strip().lower()

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT ghost_override, override_set_at FROM companies WHERE lower(company) = ?",
            (company_key,),
        ).fetchone()

    if row is None or not row["ghost_override"]:
        return None

    try:
        set_at = datetime.fromisoformat(row["override_set_at"])
        if (datetime.now() - set_at).days > ttl_days:
            return None
    except (ValueError, TypeError):
        return None

    return row["ghost_override"]


def set_ghost_override(company: str, override: str, source: str = "email_reply"):
    """
    Write or refresh a ghost override for a company.

    Args:
        company:  Company name (stored and matched case-insensitively).
        override: 'confirmed_real' or 'confirmed_ghost'.
        source:   How the override was determined (default: 'email_reply').
    """
    if override not in ("confirmed_real", "confirmed_ghost"):
        raise ValueError(f"Invalid override value: {override!r}")

    company_key = company.strip().lower()

    with _get_conn() as conn:
        conn.execute(
            """INSERT INTO companies (company, ghost_override, override_set_at, override_source)
               VALUES (?, ?, datetime('now'), ?)
               ON CONFLICT(company) DO UPDATE SET
                   ghost_override  = excluded.ghost_override,
                   override_set_at = excluded.override_set_at,
                   override_source = excluded.override_source""",
            (company_key, override, source),
        )


def get_company_posting_context(company: str, title: str, current_url: str = "") -> dict:
    """
    Return posting history context for the hypothesis scoring prompt.

    Queries job_cache for prior entries from the same company (excluding the
    current job by URL) and returns:
      role_repost_count  -- prior postings with a similar title (Jaccard >= 0.5)
      company_open_roles -- distinct role titles from this company in the cache
      company_is_new     -- True if no prior history exists for this company
    """
    company_key = company.strip().lower()
    title_words = set(title.lower().split()) if title else set()

    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT title FROM job_cache WHERE lower(company) = ? AND url != ?",
            (company_key, current_url or ""),
        ).fetchall()

    if not rows:
        return {"role_repost_count": 0, "company_open_roles": 0, "company_is_new": True}

    role_repost_count = 0
    distinct_titles = set()
    for row in rows:
        cached_title = row["title"] or ""
        distinct_titles.add(cached_title.strip().lower())
        if title_words:
            cached_words = set(cached_title.lower().split())
            union = title_words | cached_words
            if union:
                overlap = len(title_words & cached_words) / len(union)
                if overlap >= 0.5:
                    role_repost_count += 1

    return {
        "role_repost_count": role_repost_count,
        "company_open_roles": len(distinct_titles),
        "company_is_new": False,
    }


def get_cached_companies() -> list[str]:
    """
    Return all distinct company names stored in job_cache.
    Used by the reply parser to match against free-text replies.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT company FROM job_cache WHERE company != '' ORDER BY company"
        ).fetchall()
    return [r["company"] for r in rows]


# ── Cache Cleanup ─────────────────────────────────────────────────────────────

def run_cache_cleanup() -> dict:
    """
    Enforce retention and size rules on the tracker database.

    Rules applied (in order):
      1. seen_jobs   — delete entries older than 90 days
      2. job_cache   — delete ALL entries for companies with no activity in 90 days
      3. job_cache   — cap each company to 10 most-recent entries; drop oldest beyond that
      4. career_page_cache — delete entries older than 90 days

    Returns a dict with the row counts affected by each rule:
      {
        "expired_seen_jobs":      int,   # rule 1 — rows removed
        "expired_companies":      int,   # rule 2 — rows removed
        "trimmed_repost_entries": int,   # rule 3 — rows removed
        "expired_career_cache":   int,   # rule 4 — rows removed
      }
    """
    stats = {
        "expired_seen_jobs":      0,
        "expired_companies":      0,
        "trimmed_repost_entries": 0,
        "expired_career_cache":   0,
    }

    with _get_conn() as conn:
        # ── Rule 1: expire seen_jobs older than 90 days ───────────────────────
        cur = conn.execute(
            f"""DELETE FROM seen_jobs
                WHERE added_at < datetime('now', '-{_COMPANY_INACTIVITY_DAYS} days')"""
        )
        stats["expired_seen_jobs"] = cur.rowcount

        # ── Rule 2: remove all entries for companies with 90-day inactivity ──
        # A company is "inactive" when its most-recent cached_at is older than
        # the threshold. We delete every row for that company, not just old ones.
        cur = conn.execute(
            f"""DELETE FROM job_cache
                WHERE lower(company) IN (
                    SELECT lower(company)
                    FROM   job_cache
                    GROUP  BY lower(company)
                    HAVING MAX(cached_at) < datetime('now', '-{_COMPANY_INACTIVITY_DAYS} days')
                )"""
        )
        stats["expired_companies"] = cur.rowcount

        # ── Rule 2: cap per-company repost history at 10 entries ─────────────
        # Keep the 10 most recent rows (by cached_at) for each company;
        # delete everything older.  The self-join counts how many rows for the
        # same company have a cached_at >= this row's cached_at.  Rows ranked
        # > 10 are removed.
        cur = conn.execute(
            f"""DELETE FROM job_cache
                WHERE rowid IN (
                    SELECT a.rowid
                    FROM   job_cache a
                    WHERE  (
                        SELECT COUNT(*)
                        FROM   job_cache b
                        WHERE  lower(b.company) = lower(a.company)
                        AND    b.cached_at      >= a.cached_at
                    ) > {_REPOST_CAP_PER_COMPANY}
                )"""
        )
        stats["trimmed_repost_entries"] = cur.rowcount

        # ── Rule 3: expire career page cache entries older than 90 days ──────
        # The 7-day TTL in ghost_detector.py drives re-checks at query time;
        # this sweep removes entries that are far past TTL and no longer useful.
        cur = conn.execute(
            f"""DELETE FROM career_page_cache
                WHERE cached_at < datetime('now', '-{_COMPANY_INACTIVITY_DAYS} days')"""
        )
        stats["expired_career_cache"] = cur.rowcount

    return stats
