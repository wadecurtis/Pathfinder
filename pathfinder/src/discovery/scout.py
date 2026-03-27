"""Automated job scout — searches job boards and sends new matches to Telegram."""

import json
import logging
import os
from datetime import datetime

from .._location import parse_locations
from ..models import JobListing
from ..profile_loader import load_settings
from ..tracker import load_seen_job_ids, save_seen_job_ids
from .scraper import search_jobs

logger = logging.getLogger(__name__)


def _ai_filter_jobs(
    jobs: list[JobListing],
    target_roles: str,
    batch_size: int = 25,
) -> list[JobListing]:
    """Use LLM to filter jobs by relevance. Sends jobs in batches for efficiency.

    Returns only jobs that the LLM considers relevant to the target roles.
    Falls back to returning all jobs if the LLM call fails.
    """
    from ..llm_client import get_llm_response

    relevant = []
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        numbered = "\n".join(
            f"{idx}. {job.title} at {job.company}" for idx, job in enumerate(batch)
        )
        prompt = (
            f"You are a strict job relevance filter for a job seeker.\n"
            f"Target roles: {target_roles}\n\n"
            f"Review each job and return ONLY the indices (0-based) of jobs that are a clear, "
            f"direct match. Be very strict — when in doubt, exclude it.\n\n"
            f"EXCLUDE: trades, electrical/mechanical engineering, civil/structural engineering, "
            f"sales, retail, food & beverage, healthcare, finance/accounting, HR, legal, "
            f"manual labour, quality assurance/testing (non-software), packaging, logistics, "
            f"underwriting, automotive, outside sales, anything not directly related to the target roles.\n\n"
            f"INCLUDE only: roles whose title clearly matches the target roles above.\n\n"
            f"Jobs:\n{numbered}\n\n"
            f"Reply with ONLY a JSON array of relevant 0-based indices, e.g. [0, 2, 5]. "
            f"If none match, reply with []."
        )
        try:
            response = get_llm_response(prompt, max_tokens=256)
            response = response.strip()
            # Extract JSON array from response
            start = response.find("[")
            end = response.rfind("]") + 1
            if start == -1 or end == 0:
                logger.warning("AI filter returned no JSON array — keeping batch as-is")
                relevant.extend(batch)
                continue
            indices = json.loads(response[start:end])
            kept = [batch[idx] for idx in indices if 0 <= idx < len(batch)]
            dropped = len(batch) - len(kept)
            logger.info(f"AI filter: kept {len(kept)}, dropped {dropped} from batch of {len(batch)}")
            relevant.extend(kept)
        except Exception as e:
            logger.warning(f"AI filter failed ({e}) — keeping batch as-is")
            relevant.extend(batch)

    return relevant


def scout_jobs(
    _metrics: dict | None = None,
    max_queries: int | None = None,
    max_per_query_override: int | None = None,
    skip_ai_filter: bool = False,
) -> list[JobListing]:
    """Run all configured search queries and return only NEW jobs not seen before.

    Optionally populates _metrics dict with funnel counts at each stage.

    Args:
        max_queries: Limit to the first N queries (None = use all).
        max_per_query_override: Cap results per query (None = use config value).
        skip_ai_filter: If True, bypass the AI relevance filter even if enabled in config.
    """
    settings = load_settings()
    scout_config = settings.get("scout", {})
    queries = scout_config.get("queries", [])
    if max_queries is not None:
        queries = queries[:max_queries]
    locations_raw = scout_config.get(
        "locations", settings["discovery"].get("default_locations", ["canada"])
    )
    locations = parse_locations(locations_raw)
    sources = scout_config.get("sources", settings.get("discovery", {}).get("default_sources", ["linkedin"]))
    max_per_query = max_per_query_override if max_per_query_override is not None else scout_config.get("max_per_query", 15)
    hours_old = scout_config.get("hours_old", 336)  # default 2 weeks
    remote_only = scout_config.get("remote_only", False)
    ai_filter = False if skip_ai_filter else scout_config.get("ai_filter", False)
    target_roles = scout_config.get("target_roles", ", ".join(queries))

    if not queries:
        logger.warning("No scout queries configured in settings.yaml")
        return []

    # Title keyword filters from config (fast pre-filter before AI)
    title_keywords = [kw.lower() for kw in scout_config.get("title_keywords", [])]
    title_exclude = [kw.lower() for kw in scout_config.get("title_exclude", [])]

    seen_ids = load_seen_job_ids()
    new_jobs = []
    raw_count = 0
    excluded_count = 0
    already_seen_count = 0

    for country, location in locations:
      for query in queries:
        logger.info(f"Scouting: '{query}' in {location or country}")
        try:
            jobs = search_jobs(
                query=query,
                location=location or country.title(),
                sources=sources,
                max_results=max_per_query,
                country=country,
                hours_old=hours_old,
            )
            raw_count += len(jobs)

            for job in jobs:
                if job.id in seen_ids:
                    already_seen_count += 1
                    continue

                title_lower = job.title.lower()

                # Fast exclude-only pre-filter (runs before AI to drop obvious mismatches)
                # title_keywords intentionally not used here — it's too strict for Indeed/Glassdoor
                # which return valid roles without "salesforce" in the title. Let AI filter handle relevance.
                if title_exclude and any(kw in title_lower for kw in title_exclude):
                    logger.info(f"Skipping '{job.title}' ({job.source}) — title excluded")
                    seen_ids.add(job.id)
                    excluded_count += 1
                    continue

                # Optional: filter remote-only
                if remote_only:
                    loc_lower = job.location.lower()
                    if not any(kw in loc_lower or kw in title_lower for kw in ["remote", "hybrid", "anywhere"]):
                        continue

                new_jobs.append(job)

        except Exception as e:
            logger.error(f"Scout error for '{query}': {e}")

    # Deduplicate by URL (same job can appear across multiple queries/sources)
    seen_urls: set[str] = set()
    deduped = []
    url_dedup_count = 0
    for job in new_jobs:
        url_key = job.url.split("?")[0].rstrip("/") if job.url else ""
        if url_key and url_key in seen_urls:
            url_dedup_count += 1
            seen_ids.add(job.id)
            continue
        if url_key:
            seen_urls.add(url_key)
        deduped.append(job)
    new_jobs = deduped
    after_dedup = len(new_jobs)
    logger.info(f"After dedup: {after_dedup} unique candidates")

    # AI relevance filter — runs once on the full candidate set
    if ai_filter and new_jobs:
        logger.info(f"Running AI relevance filter on {len(new_jobs)} candidates...")
        new_jobs = _ai_filter_jobs(new_jobs, target_roles)

    # Mark all as seen after filtering
    for job in new_jobs:
        seen_ids.add(job.id)

    save_seen_job_ids(seen_ids)
    logger.info(f"Scout complete: {len(new_jobs)} new jobs found")

    if _metrics is not None:
        _metrics["raw_scraped"]     = raw_count
        _metrics["already_seen"]    = already_seen_count
        _metrics["excluded"]        = excluded_count
        _metrics["url_dedup"]       = url_dedup_count
        _metrics["after_dedup"]     = after_dedup
        _metrics["after_ai_filter"] = len(new_jobs)

    return new_jobs


def format_scout_message(jobs: list[JobListing], batch_size: int = 5) -> list[str]:
    """Format new jobs into Telegram messages (split into batches to avoid message limits)."""
    if not jobs:
        return []

    messages = []
    for i in range(0, len(jobs), batch_size):
        batch = jobs[i : i + batch_size]
        lines = [f"Found {len(jobs)} new jobs for you:\n"] if i == 0 else []

        for j, job in enumerate(batch, start=i + 1):
            lines.append(f"*{j}. {job.title}*")
            lines.append(f"   {job.company} — {job.location}")
            if job.source:
                lines.append(f"   Source: {job.source}")
            if job.url:
                lines.append(f"   {job.url}")
            lines.append("")

        messages.append("\n".join(lines))

    return messages
