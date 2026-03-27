"""Application tracker — seen-jobs dedup and job cache (SQLite)."""

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tracker.db")


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
