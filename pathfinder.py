"""
pathfinder.py — Daily job digest. Searches, filters, and scores roles. Emails strong fits.

Usage:
    python pathfinder.py           # Full run — search, score, email digest
    python pathfinder.py --test    # Lightweight test — 2 queries, 5 results each,
                                   # skip AI filter, score top 5, print to terminal
                                   # and send the real HTML email. Use this to
                                   # validate config and rendering before the first
                                   # GitHub Actions run.
    python pathfinder.py --preview # Send a sample email with dummy data to check
                                   # rendering without querying any job boards.
"""

import argparse
import logging
import os
import sys
from collections import Counter

# Add pathfinder to path so we can use its modules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pathfinder"))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "pathfinder", ".env"), override=True)

from src.digest import build_html, build_no_results_email, print_digest, send_email
from src.discovery.scout import scout_jobs
from src.ghost_detector import detect_ghost, find_careers_page_url
from src.reply_parser import parse_replies
from src.salesforce import push_to_salesforce
from src.scorer import score_all
from src.tracker import run_cache_cleanup

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


# Sample data used by --preview and as --test fallback when no listings are found
SAMPLE_JOBS = [
    {"title": "Salesforce Implementation Consultant", "company": "Acme Consulting",
     "location": "Remote - Canada", "url": "https://linkedin.com/jobs/view/123",
     "score": "YES", "reason": "Full lifecycle Sales Cloud delivery, SMB focus, remote-friendly - strong match.",
     "hypothesis_category": "New capability",
     "hypothesis_signal": "Role is titled 'founding consultant' and reports directly to the VP of Delivery - this seat is building the practice, not backfilling it. Candidate's self-implementation background is the exact proof point.",
     "ghost_detection": "clean", "careers_page_url": "https://acmeconsulting.com/careers"},
    {"title": "Salesforce Solutions Consultant", "company": "CloudCo",
     "location": "Vancouver, BC (Hybrid)", "url": "https://linkedin.com/jobs/view/456",
     "score": "YES", "reason": "Agentforce implementation valued, declarative-only, mid-market clients.",
     "hypothesis_category": "Capacity",
     "hypothesis_signal": "Three open roles posted in the same month suggests a pipeline problem, not a single gap. Candidate adds immediate delivery capacity without a ramp period.",
     "ghost_detection": "Ghost Likely"},
    {"title": "CRM Implementation Consultant", "company": "Ridge Partners",
     "location": "Remote - Canada", "url": "https://linkedin.com/jobs/view/789",
     "score": "MAYBE", "reason": "Platform not specified - could be Salesforce, could be HubSpot.",
     "hypothesis_category": "Unclear",
     "hypothesis_signal": "Posting uses generic CRM language throughout with no platform named - either intentionally platform-agnostic or written by someone outside the team. Worth a quick look at their tech stack before applying.",
     "ghost_detection": "Unverified"},
    {"title": "Salesforce Admin", "company": "BuildCorp",
     "location": "Remote - Canada", "url": "https://linkedin.com/jobs/view/101",
     "score": "MAYBE", "reason": "Admin-level scope, but admin + consulting hybrid is common at this size.",
     "hypothesis_category": "Backfill",
     "hypothesis_signal": "Single headcount, no growth language - this is a backfill. Low churn risk for candidate.",
     "ghost_detection": "Low Risk"},
    {"title": "Salesforce Functional Consultant", "company": "NorthPeak Group",
     "location": "Remote - Canada", "url": "https://linkedin.com/jobs/view/202",
     "score": "MAYBE", "reason": "Role scope aligns but platform stack is ambiguous - follow up required.",
     "hypothesis_category": "Unclear",
     "hypothesis_signal": "No tech stack named and the role is listed under both IT and Sales divisions - likely an internal headcount debate still in progress.",
     "ghost_detection": "clean"},
    {"title": "Salesforce Developer", "company": "DevShop Inc",
     "location": "Remote - Canada", "url": "https://linkedin.com/jobs/view/303",
     "score": "NO", "reason": "Requires Apex and LWC development - hard disqualifier.",
     "hypothesis_category": "", "hypothesis_signal": "",
     "ghost_detection": "clean", "careers_page_url": None},
    {"title": "Salesforce CPQ Consultant", "company": "PriceCo",
     "location": "Toronto, ON (On-site)", "url": "https://linkedin.com/jobs/view/404",
     "score": "NO", "reason": "On-site Toronto only with no remote option - location disqualifier.",
     "hypothesis_category": "", "hypothesis_signal": "",
     "ghost_detection": "clean", "careers_page_url": None},
    {"title": "Senior Salesforce Architect", "company": "GlobalSI",
     "location": "Remote - Canada", "url": "https://linkedin.com/jobs/view/505",
     "score": "NO", "reason": "Employer is a global SI - disqualified regardless of role fit.",
     "hypothesis_category": "", "hypothesis_signal": "",
     "ghost_detection": "clean", "careers_page_url": None},
]
SAMPLE_METRICS = {
    "raw_scraped": 87, "already_seen": 12, "excluded": 5,
    "after_dedup": 70, "after_ai_filter": 18,
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true",
                        help="Lightweight test: 2 queries, 5 results each, skip AI filter, "
                             "score top 5, print to terminal and send the real HTML email.")
    parser.add_argument("--preview", action="store_true",
                        help="Send a sample email with dummy data to check rendering. "
                             "No job board queries, no scoring.")
    args = parser.parse_args()

    if args.preview:
        logger.info("\n[Pathfinder] PREVIEW MODE — sending sample email...")
        subject, html = build_html(SAMPLE_JOBS, SAMPLE_METRICS)
        send_email(subject, html)
        logger.info("[Pathfinder] Preview email sent.")
        return

    if args.test:
        logger.info("\n[Pathfinder] TEST MODE — 2 queries · 5 results each · skip AI filter · score top 5 · email sent")
    else:
        logger.info("\n[Pathfinder] Starting...")

    if not os.getenv("GROQ_API_KEY"):
        logger.error("[Pathfinder] ERROR: GROQ_API_KEY is not set. Cannot score jobs.")
        sys.exit(1)

    # 0a. Reply parser — process inbound feedback before anything else so
    #     ghost overrides are live for both cleanup and scoring this run.
    reply_stats = parse_replies()
    if reply_stats["emails_read"]:
        logger.info(
            f"[Reply] {reply_stats['emails_read']} feedback email(s) read, "
            f"{reply_stats['overrides_set']} ghost override(s) written"
        )

    # 0b. Cache cleanup — enforce retention rules before any work begins
    cleanup = run_cache_cleanup()
    total_cleaned = sum(cleanup.values())
    if total_cleaned:
        logger.info(
            f"[Cache] Cleanup: "
            f"{cleanup['expired_companies']} company records expired (90d inactivity)  "
            f"{cleanup['trimmed_repost_entries']} repost entries trimmed (10-entry cap)  "
            f"{cleanup['expired_career_cache']} career page cache entries expired"
        )
    else:
        logger.info("[Cache] Cleanup: nothing to remove")

    # 1. Scout — search + optional AI filter
    logger.info("[Pathfinder] Searching...")
    metrics = {}
    if args.test:
        jobs = scout_jobs(_metrics=metrics, max_queries=2, max_per_query_override=5, skip_ai_filter=True)
    else:
        jobs = scout_jobs(_metrics=metrics)

    source_counts = Counter(j.source or "unknown" for j in jobs)
    logger.info(f"[Pathfinder] {len(jobs)} new listings to score — by source: {dict(source_counts)}")

    if not jobs:
        logger.info("[Pathfinder] No new listings found.")
        if args.test:
            logger.info("[Pathfinder] Sending digest showing what happened.")
            send_email(*build_no_results_email(metrics))
        return

    # 2. Score — cap at 5 in test mode to limit token usage
    jobs_to_score = jobs[:5] if args.test else jobs
    if args.test and len(jobs) > 5:
        logger.info(f"[Pathfinder] Scoring top 5 of {len(jobs)} (test mode cap)")

    scored = score_all(jobs_to_score)
    metrics["scored_yes"]   = sum(1 for j in scored if j["score"] == "YES")
    metrics["scored_maybe"] = sum(1 for j in scored if j["score"] == "MAYBE")
    metrics["scored_no"]    = sum(1 for j in scored if j["score"] == "NO")
    relevant = [j for j in scored if j["score"] in ("YES", "MAYBE")]

    # Ghost detection + careers page discovery — run only on QUALIFY and NEUTRAL results
    logger.info(f"[Ghost] Running ghost detection on {len(relevant)} relevant listings...")
    _GHOST_NOTES = {
        "Unverified":   "Repost signal detected - verify this role is still open before applying.",
        "Ghost Likely": "Strong repost history - this role may not be actively filling.",
    }
    for job in relevant:
        result = detect_ghost(job)
        job["ghost_detection"] = result
        if result != "clean":
            logger.info(f"  [{result}] {job['company']}: {job['title']}")
        if result in _GHOST_NOTES and job.get("hypothesis_signal"):
            job["hypothesis_signal"] = job["hypothesis_signal"] + " " + _GHOST_NOTES[result]
        careers_url = find_careers_page_url(job.get("company", ""), job.get("url", ""))
        job["careers_page_url"] = careers_url
        if careers_url:
            logger.info(f"  [Careers] {job['company']}: {careers_url}")
    # Ensure NO-scored jobs have the keys set to avoid KeyError in templates
    for job in scored:
        job.setdefault("ghost_detection", "clean")
        job.setdefault("careers_page_url", None)

    if not relevant:
        logger.info("[Pathfinder] Nothing relevant this run.")
        logger.info(f"[Pathfinder] Funnel: {metrics.get('raw_scraped',0)} scraped → {len(jobs_to_score)} scored → 0 relevant")
        return

    # 3. Output — always print; also send email (test mode sends the real HTML for rendering checks)
    print_digest(scored, metrics)
    subject, html = build_html(scored, metrics)
    if subject:
        send_email(subject, html)

    # 4. Push YES + MAYBE to Salesforce Career Pipeline
    push_to_salesforce(scored)

    logger.info(f"[Pathfinder] Done. {len([j for j in relevant if j['score']=='YES'])} strong fits, "
                f"{len([j for j in relevant if j['score']=='MAYBE'])} maybes.")


if __name__ == "__main__":
    main()
