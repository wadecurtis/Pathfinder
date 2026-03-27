"""Job discovery using python-jobspy to search multiple job boards."""

import hashlib
import logging
import os

import pandas as pd
from jobspy import scrape_jobs

from ..models import JobListing
from ..tracker import save_job_to_cache

logger = logging.getLogger(__name__)


def search_jobs(
    query: str,
    location: str = "Calgary, AB, Canada",
    sources: list[str] | None = None,
    max_results: int = 30,
    country: str = "Canada",
    hours_old: int = 336,
) -> list[JobListing]:
    """Search job boards and return structured job listings."""
    if sources is None:
        sources = ["indeed", "linkedin", "glassdoor"]

    # Search each source separately so one failure doesn't block the others
    frames = []
    for source in sources:
        try:
            kwargs = dict(
                site_name=[source],
                search_term=query,
                location=location,
                results_wanted=max_results,
                country_indeed=country,
                hours_old=hours_old,
                linkedin_fetch_description=True,
            )
            # Google Jobs ignores search_term and requires google_search_term
            if source == "google":
                kwargs["google_search_term"] = f"{query} jobs in {location}"
            df = scrape_jobs(**kwargs)
            if not df.empty:
                frames.append(df)
                logger.info("%s returned %d results for '%s'", source, len(df), query)
            else:
                logger.warning("%s returned 0 results for '%s'", source, query)
        except Exception as e:
            logger.warning("JobSpy %s scrape failed: %s", source, e)

    if not frames:
        return []

    df = pd.concat(frames, ignore_index=True)

    def _clean(val, default=""):
        """Convert pandas value to string, treating NaN as empty."""
        s = str(val) if val is not None else default
        return default if s in ("nan", "None", "NaN") else s

    jobs = []
    for _, row in df.iterrows():
        job_id = hashlib.md5(
            f"{row.get('title', '')}{row.get('company', '')}{row.get('job_url', '')}".encode()
        ).hexdigest()[:12]

        job = JobListing(
            id=job_id,
            title=_clean(row.get("title")),
            company=_clean(row.get("company")),
            location=_clean(row.get("location")),
            url=_clean(row.get("job_url")),
            description=_clean(row.get("description")),
            date_posted=_clean(row.get("date_posted")),
            source=_clean(row.get("site")),
            salary=_clean(row.get("min_amount")),
        )
        jobs.append(job)
        save_job_to_cache(job.model_dump())

    return jobs
