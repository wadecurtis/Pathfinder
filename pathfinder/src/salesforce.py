"""Push scored job listings to Salesforce Career Pipeline."""

import logging
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def push_to_salesforce(jobs: list[dict]):
    """Push YES and MAYBE jobs to Salesforce Career Pipeline. Skips if credentials not set."""
    sf_user  = os.getenv("SF_USERNAME")
    sf_pass  = os.getenv("SF_PASSWORD")
    sf_token = os.getenv("SF_SECURITY_TOKEN")

    if not all([sf_user, sf_pass, sf_token]):
        return  # not configured — silent skip

    try:
        from simple_salesforce import Salesforce
    except ImportError:
        logger.warning("[Salesforce] simple-salesforce not installed — skipping.")
        return

    try:
        sf = Salesforce(username=sf_user, password=sf_pass, security_token=sf_token)
    except Exception as e:
        logger.error(f"[Salesforce] Connection failed — {e}")
        return

    close_date = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")

    pushed = skipped = 0
    for job in jobs:
        if job["score"] not in ("YES", "MAYBE"):
            continue

        # Source mapping — Pathfinder source names to CP_Source__c picklist values
        src = (job.get("source") or "").lower()
        cp_source = (
            "LinkedIn"  if src == "linkedin"                   else
            "Job Board" if src in ("indeed", "glassdoor")      else
            "Other"
        )

        # Work type mapping — inferred from location string
        loc = (job.get("location") or "").lower()
        cp_work_type = (
            "Remote" if "remote" in loc else
            "Hybrid" if "hybrid" in loc else
            "On-Site"
        )

        # Stage mapping — YES is actively pursue, MAYBE is if you have time
        stage = "Job Identified" if job["score"] == "YES" else "If you have time"

        # Find or create Account for the company
        company_escaped = job["company"].replace("'", "\\'")
        acct = sf.query(f"SELECT Id FROM Account WHERE Name = '{company_escaped}' LIMIT 1")
        if acct["totalSize"] > 0:
            account_id = acct["records"][0]["Id"]
        else:
            account_id = sf.Account.create({"Name": job["company"]})["id"]

        # Skip if this URL is already in the pipeline (don't overwrite stage)
        url_escaped = job["url"].replace("'", "\\'")
        existing = sf.query(
            f"SELECT Id FROM Opportunity WHERE Job_Posting_URL__c = '{url_escaped}' LIMIT 1"
        )
        if existing["totalSize"] > 0:
            skipped += 1
            continue

        try:
            description = job["reason"]
            if job.get("hypothesis_category") and job.get("hypothesis_signal"):
                description += f"\n\nHypothesis ({job['hypothesis_category']}): {job['hypothesis_signal']}"

            opp_data = {
                "Name":               f"{job['title']} - {job['company']}",
                "AccountId":          account_id,
                "StageName":          stage,
                "CloseDate":          close_date,
                "Description":        description,
                "LeadSource":         "Pathfinder",
                "Job_Posting_URL__c": job["url"],
                "CP_Source__c":       cp_source,
                "CP_Work_Type__c":    cp_work_type,
            }
            ghost_state = job.get("ghost_detection", "clean")
            if ghost_state and ghost_state != "clean":
                opp_data["Ghost_Detection__c"] = ghost_state
            sf.Opportunity.create(opp_data)
            pushed += 1
        except Exception as e:
            logger.error(f"[Salesforce] Failed — {job['company']}: {job['title']} — {e}")

    logger.info(f"[Salesforce] {pushed} new opportunities pushed, {skipped} already in pipeline.")
