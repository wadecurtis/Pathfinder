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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS career_page_cache (
                company TEXT NOT NULL,
                title   TEXT NOT NULL,
                result  INTEGER,
                cached_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (company, title)
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

def get_career_page_cache(company: str, title: str) -> tuple[bool | None, bool]:
    """
    Look up a career page check result from the cache.

    Returns (result, is_fresh) where:
      result   — True (found) / False (not found) / None (inconclusive)
      is_fresh — True if the entry is within the 7-day TTL

    Returns (None, False) when no entry exists.
    """
    company_key = company.strip().lower()
    title_key   = title.strip().lower()

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT result, cached_at FROM career_page_cache WHERE company = ? AND title = ?",
            (company_key, title_key),
        ).fetchone()

    if row is None:
        return None, False

    try:
        cached_dt = datetime.fromisoformat(row["cached_at"])
        is_fresh  = (datetime.now() - cached_dt).days < _CAREER_CACHE_TTL_DAYS
    except (ValueError, TypeError):
        is_fresh = False

    # SQLite stores NULL as None; 1 → True, 0 → False
    raw = row["result"]
    result = None if raw is None else bool(raw)
    return result, is_fresh


def set_career_page_cache(company: str, title: str, result: bool | None):
    """Insert or refresh a career page check result."""
    company_key = company.strip().lower()
    title_key   = title.strip().lower()
    stored      = None if result is None else (1 if result else 0)

    with _get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO career_page_cache
               (company, title, result, cached_at)
               VALUES (?, ?, ?, datetime('now'))""",
            (company_key, title_key, stored),
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
      1. job_cache   — delete ALL entries for companies with no activity in 90 days
      2. job_cache   — cap each company to 10 most-recent entries; drop oldest beyond that
      3. career_page_cache — delete entries older than 90 days

    Returns a dict with the row counts affected by each rule:
      {
        "expired_companies":      int,   # rule 1 — rows removed
        "trimmed_repost_entries": int,   # rule 2 — rows removed
        "expired_career_cache":   int,   # rule 3 — rows removed
      }
    """
    stats = {
        "expired_companies":      0,
        "trimmed_repost_entries": 0,
        "expired_career_cache":   0,
    }

    with _get_conn() as conn:
        # ── Rule 1: remove all entries for companies with 90-day inactivity ──
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
