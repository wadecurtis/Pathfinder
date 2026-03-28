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
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml

# Add pathfinder to path so we can use its modules directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "pathfinder"))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "pathfinder", ".env"), override=True)
from src.discovery.scout import scout_jobs
from src.ghost_detector import detect_ghost
from src.llm_client import get_llm_response
from src.models import JobListing
from src.reply_parser import parse_replies
from src.tracker import run_cache_cleanup


# ── Load config ───────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(
            f"config.yaml not found at {config_path}\n"
            "Copy config.example.yaml to config.yaml and fill it in."
        )
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for section in ("profile", "scoring", "search"):
            if not config or section not in config:
                raise ValueError(f"config.yaml is missing the required '{section}' section.")
        return config
    except yaml.YAMLError as e:
        raise ValueError(f"config.yaml is malformed: {e}")


def build_scoring_prompt(config: dict) -> str:
    profile  = config.get("profile", {})
    scoring  = config.get("scoring", {})

    name           = profile.get("name", "the candidate")
    framing        = profile.get("framing", "")
    highlights     = profile.get("highlights", [])
    certs_held     = profile.get("certifications_held", [])
    certs_progress = profile.get("certifications_in_progress", [])
    languages      = profile.get("languages", ["English"])
    loc_prefs      = profile.get("location_prefs", {})
    base_location  = loc_prefs.get("base", "")
    hybrid_ok      = loc_prefs.get("hybrid_ok", [])

    qualify    = scoring.get("qualify", [])
    neutral    = scoring.get("neutral", [])
    disqualify = scoring.get("disqualify", [])

    highlights_str     = "\n".join(f"- {h}" for h in highlights)
    certs_held_str     = ", ".join(certs_held)
    certs_progress_str = ", ".join(certs_progress)
    languages_str      = ", ".join(languages)
    hybrid_str         = ", ".join(hybrid_ok)
    qualify_str        = "\n".join(f"- {q}" for q in qualify)
    neutral_str        = "\n".join(f"- {n}" for n in neutral)
    disqualify_str     = "\n".join(f"- {d}" for d in disqualify)

    return f"""
You are evaluating a job posting for this specific candidate:

CANDIDATE: {name} — {framing}
{highlights_str}
- Certifications held: {certs_held_str}
- Currently completing: {certs_progress_str}
- Languages: {languages_str}
- Location: {base_location} — open to remote or {hybrid_str} hybrid

JOB TO EVALUATE:
Title: {{title}}
Company: {{company}}
Location: {{location}}
Description: {{description}}

─── SCORING FRAMEWORK ───────────────────────────────────────

QUALIFY — indicators that push toward YES:
{qualify_str}

NEUTRAL — present but not disqualifying:
{neutral_str}

DISQUALIFY — any one of these scores NO immediately:
{disqualify_str}

─── SCORING RULES ────────────────────────────────────────────

Score YES   — qualify criteria clearly outweigh neutrals and NO disqualifier is present.
Score MAYBE — qualify criteria are present but exactly one neutral applies OR one potential
              disqualifier is ambiguous (e.g. cert requirement may be preferred not mandatory,
              or a cert the candidate is actively completing: {certs_progress_str}).
Score NO    — any hard disqualifier is present, regardless of qualify criteria.

LOCATION RULE (apply before scoring):
- Score location OK if: fully remote, remote-first, or remote-friendly anywhere in Canada/globally.
- Score location OK if: hybrid or in-office in {hybrid_str}.
- Score location NO if: hybrid or in-office in any city outside {hybrid_str} with no remote option stated.
- If location is unstated or ambiguous, assume remote is possible (location OK).
A location NO is a hard disqualifier — score the overall job NO.

Do not hedge. Make a call.

Respond in exactly this format:
SCORE: YES / MAYBE / NO
REASON: one sentence naming the key qualifier or disqualifier that decided the score

If SCORE is YES or MAYBE, also output:
HYPOTHESIS_CATEGORY: pick exactly one — Backfill | Capacity | New capability | Recovery | Strategic bet | Unclear
HYPOTHESIS_SIGNAL: one to two sentences — what specific signals in the posting reveal why they are hiring, and why this candidate fills that exact gap
"""


CONFIG = load_config()
SCORING_PROMPT_TEMPLATE = build_scoring_prompt(CONFIG)


def score_job(job: JobListing) -> tuple[str, str, str, str]:
    """Score a single job. Returns (score, reason, hypothesis_category, hypothesis_signal)."""
    prompt = SCORING_PROMPT_TEMPLATE.format(
        title=job.title,
        company=job.company,
        location=job.location,
        description=(job.description or "")[:3000],
    )
    try:
        response = get_llm_response(prompt, max_tokens=250)
        score  = "NO"
        reason = ""
        hyp_category = ""
        hyp_signal   = ""
        for line in response.strip().splitlines():
            if line.startswith("SCORE:"):
                score = line.replace("SCORE:", "").strip()
            elif line.startswith("REASON:"):
                reason = line.replace("REASON:", "").strip()
            elif line.startswith("HYPOTHESIS_CATEGORY:"):
                hyp_category = line.replace("HYPOTHESIS_CATEGORY:", "").strip()
            elif line.startswith("HYPOTHESIS_SIGNAL:"):
                hyp_signal = line.replace("HYPOTHESIS_SIGNAL:", "").strip()
        return score, reason, hyp_category, hyp_signal
    except Exception as e:
        return "MAYBE", f"Could not score — review manually ({e})", "", ""


def score_all(jobs: list[JobListing]) -> list[dict]:
    """Score all jobs and return enriched dicts."""
    results = []
    print(f"[Score] Scoring {len(jobs)} listings...")
    for job in jobs:
        score, reason, hyp_category, hyp_signal = score_job(job)
        icon = "YES" if score == "YES" else ("~~" if score == "MAYBE" else "NO")
        desc_len = len(job.description or "")
        print(f"  [{icon}] {job.company}: {job.title} (desc: {desc_len} chars)")
        print(f"       {reason}")
        if hyp_category:
            print(f"       [{hyp_category}] {hyp_signal}")
        results.append({
            "title":              job.title,
            "company":            job.company,
            "location":           job.location,
            "url":                job.url,
            "source":             job.source,
            "date_posted":        job.date_posted,
            "score":              score,
            "reason":             reason,
            "hypothesis_category": hyp_category,
            "hypothesis_signal":   hyp_signal,
        })
    return results


# ── Email digest ──────────────────────────────────────────────────────────────

def build_html(jobs: list[dict], metrics: dict) -> tuple[str, str]:
    yes_jobs   = [j for j in jobs if j["score"] == "YES"]
    maybe_jobs = [j for j in jobs if j["score"] == "MAYBE"]
    no_jobs    = [j for j in jobs if j["score"] == "NO"]

    if not yes_jobs and not maybe_jobs:
        return None, None

    # ── Color tokens — light palette (Outlook-safe defaults) ─────────────────
    # Outlook Classic ignores @media queries, so bgcolor attrs and inline styles
    # define the light version. Dark mode is layered on top via @media for
    # Gmail, Apple Mail, and iOS — which do support prefers-color-scheme.
    BG        = "#F0F4F8"   # page background
    CARD      = "#FFFFFF"   # card / funnel surface
    TEXT      = "#0D2F4F"   # primary text — dark navy
    MUTED     = "#64748B"   # secondary text
    DIM       = "#94A3B8"   # tertiary text
    TEAL      = "#1D9E75"   # YES accent, teal labels
    BORDER    = "#CBD5E1"   # visible borders
    MAYBE_ACC = "#94A3B8"   # MAYBE left accent bar
    MAYBE_BTN = "#64748B"   # MAYBE button
    BTN_TEXT  = "#FFFFFF"   # button label (white on any colored bg)

    date_str = datetime.now().strftime("%B %d, %Y")
    subject  = f"Pathfinder — {len(yes_jobs)} strong fits, {len(maybe_jobs)} maybes ({date_str})"

    raw          = metrics.get("raw_scraped", 0)
    already_seen = metrics.get("already_seen", 0)
    excluded     = metrics.get("excluded", 0)
    url_dedup    = metrics.get("url_dedup", 0)
    after_dedup  = metrics.get("after_dedup", 0)
    scored       = metrics.get("after_ai_filter", len(jobs))
    ai_dropped   = max(after_dedup - scored, 0)

    # ── Dark-mode overrides via @media ────────────────────────────────────────
    # Plain string — CSS braces don't need escaping outside an f-string.
    css = """<style type="text/css">
  @media screen and (prefers-color-scheme: dark) {
    .bg-body       { background-color: #0D2F4F !important; }
    .bg-card       { background-color: #111827 !important; }
    .bg-funnel     { background-color: #111827 !important; }
    .bg-hypothesis { background-color: #0D2B1A !important; }
    .t-primary     { color: #FFFFFF !important; }
    .t-muted       { color: #6B7A8D !important; }
    .t-dim         { color: #94A3B8 !important; }
    .t-reason  { color: #FFFFFF !important; }
    .t-teal    { color: #1D9E75 !important; }
    .border-dim    { border-top-color: #1E3A52 !important; }
    .border-footer { border-top-color: #1E3A52 !important; }
    .accent-maybe  { background-color: #1E3A52 !important; }
    .btn-maybe     { background-color: #1E3A52 !important; }
  }
</style>"""

    # ── Funnel block ──────────────────────────────────────────────────────────
    funnel_html = f"""<tr>
  <td style="padding-bottom:24px;">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
      <tr>
        <td bgcolor="{CARD}" class="bg-funnel"
            style="background-color:{CARD};border-radius:10px;padding:18px 20px;
                   border:1px solid {BORDER};">
          <p style="margin:0 0 10px;font-size:12px;text-transform:uppercase;
                    letter-spacing:1.5px;color:{MUTED};font-weight:600;"
             class="t-muted">Pipeline &mdash; {date_str}</p>
          <p style="margin:0;font-size:14px;color:{MUTED};line-height:1.9;"
             class="t-muted">
            {raw}&nbsp;scraped &nbsp;&middot;&nbsp;
            {already_seen}&nbsp;already seen &nbsp;&middot;&nbsp;
            {excluded}&nbsp;excluded &nbsp;&middot;&nbsp;
            {url_dedup}&nbsp;duplicate URL &nbsp;&middot;&nbsp;
            {ai_dropped}&nbsp;AI filtered
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td style="padding-top:12px;border-top:1px solid {BORDER};
                         font-size:14px;line-height:1.9;" class="border-dim">
                <span style="color:{TEXT};font-weight:600;"
                      class="t-primary">{scored}&nbsp;scored</span>
                &nbsp;&nbsp;
                <span style="color:{TEAL};font-weight:700;"
                      class="t-teal">{len(yes_jobs)}&nbsp;yes</span>
                &nbsp;&middot;&nbsp;
                <span style="color:{MUTED};"
                      class="t-muted">{len(maybe_jobs)}&nbsp;maybe</span>
                &nbsp;&middot;&nbsp;
                <span style="color:{DIM};"
                      class="t-dim">{len(no_jobs)}&nbsp;no</span>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td>
</tr>"""

    # ── Ghost badge colors ────────────────────────────────────────────────────
    GHOST_BADGE = {
        "Verified":     ("#166534", "#F0FDF4"),   # green text on green-50
        "Low Risk":     ("#92400E", "#FEF3C7"),   # amber text on amber-50
        "Unverified":   ("#9A3412", "#FFEDD5"),   # orange text on orange-50
        "Ghost Likely": ("#991B1B", "#FEE2E2"),   # red text on red-50
    }

    # ── Card row builder ──────────────────────────────────────────────────────
    def card_row(job, strong=False):
        accent_bg     = TEAL      if strong else MAYBE_ACC
        accent_class  = ""        if strong else "accent-maybe"
        btn_bg        = TEAL      if strong else MAYBE_BTN
        btn_class     = ""        if strong else "btn-maybe"
        company_color = TEAL      if strong else MUTED
        company_class = "t-teal"  if strong else "t-muted"

        # Ghost detection badge (top-right of title row, hidden when clean)
        ghost_state = job.get("ghost_detection", "clean")
        if ghost_state in GHOST_BADGE:
            badge_fg, badge_bg = GHOST_BADGE[ghost_state]
            badge_html = (
                f'<td align="right" valign="top" style="padding-left:8px;white-space:nowrap;">'
                f'<span style="display:inline-block;background-color:{badge_bg};color:{badge_fg};'
                f'font-size:10px;font-weight:700;letter-spacing:0.5px;padding:3px 7px;'
                f'border-radius:4px;white-space:nowrap;">{ghost_state}</span>'
                f'</td>'
            )
        else:
            badge_html = ""

        hypothesis_html = ""
        if job.get("hypothesis_category") and job.get("hypothesis_signal"):
            hypothesis_html = f"""
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
                 style="margin:0 0 16px;">
            <tr>
              <td class="bg-hypothesis"
                  style="padding:10px 12px;background-color:#F0F9F5;border-radius:6px;
                         border-left:3px solid {TEAL};">
                <p style="margin:0 0 4px;font-size:15px;text-transform:uppercase;
                           letter-spacing:1.2px;color:{TEAL};font-weight:700;"
                   class="t-teal">Hypothesis &middot; {job['hypothesis_category']}</p>
                <p style="margin:0;font-size:15px;font-weight:700;color:{TEXT};line-height:1.5;"
                   class="t-primary">{job['hypothesis_signal']}</p>
              </td>
            </tr>
          </table>"""

        # Title row: job title left, ghost badge right (table layout for Outlook compat)
        title_row = (
            f'<table width="100%" cellpadding="0" cellspacing="0" role="presentation"'
            f' style="margin:0 0 3px;">'
            f'<tr>'
            f'<td style="font-size:17px;font-weight:700;color:{TEXT};" class="t-primary">'
            f'{job["title"]}</td>'
            f'{badge_html}'
            f'</tr></table>'
        )

        return f"""<tr>
  <td style="padding-bottom:10px;">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
      <tr>
        <!-- Left accent bar -->
        <td width="4" bgcolor="{accent_bg}" class="{accent_class}"
            style="background-color:{accent_bg};font-size:0;line-height:0;">&nbsp;</td>
        <!-- Card body -->
        <td bgcolor="{CARD}" class="bg-card"
            style="background-color:{CARD};padding:18px 20px;border-radius:0 10px 10px 0;
                   border:1px solid {BORDER};border-left:none;">
          {title_row}
          <p style="margin:0 0 2px;font-size:15px;font-weight:600;color:{company_color};"
             class="{company_class}">{job['company']}</p>
          <p style="margin:0 0 10px;font-size:13px;color:{MUTED};"
             class="t-muted">{job['location']}</p>
          <p style="margin:0 0 12px;font-size:15px;font-weight:500;color:#111111;line-height:1.5;"
             class="t-reason">{job['reason']}</p>
          {hypothesis_html}
          <!-- Button: table wrapper required for bgcolor in Outlook -->
          <table cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td bgcolor="{btn_bg}" class="{btn_class}"
                  style="background-color:{btn_bg};border-radius:7px;">
                <a href="{job['url']}"
                   style="display:inline-block;color:{BTN_TEXT};padding:8px 16px;
                          text-decoration:none;font-size:14px;font-weight:600;line-height:1;">
                  View Role &#8594;</a>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </td>
</tr>"""

    # ── Sections ──────────────────────────────────────────────────────────────
    yes_section = ""
    if yes_jobs:
        yes_section = f"""<tr>
  <td style="padding:16px 0 10px;">
    <p style="margin:0;font-size:12px;text-transform:uppercase;letter-spacing:1.5px;
              color:{TEAL};font-weight:700;" class="t-teal">Qualify ({len(yes_jobs)})</p>
  </td>
</tr>""" + "".join(card_row(j, strong=True) for j in yes_jobs)

    maybe_section = ""
    if maybe_jobs:
        maybe_section = f"""<tr>
  <td style="padding:16px 0 10px;">
    <p style="margin:0;font-size:12px;text-transform:uppercase;letter-spacing:1.5px;
              color:{MUTED};font-weight:700;" class="t-muted">Worth a Look ({len(maybe_jobs)})</p>
  </td>
</tr>""" + "".join(card_row(j, strong=False) for j in maybe_jobs)

    no_section = ""
    if no_jobs:
        def no_row(job, last=False):
            border = "" if last else f"border-bottom:1px solid {BORDER};"
            return f"""<tr>
  <td style="padding:11px 16px;{border}">
    <p style="margin:0 0 2px;font-size:14px;font-weight:600;color:{TEXT};" class="t-primary">{job['company']} &middot; {job['title']}</p>
    <p style="margin:0;font-size:13px;color:{MUTED};line-height:1.4;" class="t-muted">{job['reason']}</p>
  </td>
</tr>"""
        rows = "".join(no_row(j, last=(i == len(no_jobs)-1)) for i, j in enumerate(no_jobs))
        no_section = f"""<tr>
  <td style="padding:16px 0 10px;">
    <p style="margin:0;font-size:12px;text-transform:uppercase;letter-spacing:1.5px;
              color:{DIM};font-weight:700;" class="t-dim">Scored NO ({len(no_jobs)})</p>
  </td>
</tr>
<tr>
  <td bgcolor="{CARD}" class="bg-card"
      style="background-color:{CARD};border-radius:10px;border:1px solid {BORDER};">
    <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
      {rows}
    </table>
  </td>
</tr>"""

    # ── Assembly ──────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <meta name="supported-color-schemes" content="light dark">
  {css}
</head>
<body style="margin:0;padding:0;background-color:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;" bgcolor="{BG}">

<table width="100%" cellpadding="0" cellspacing="0" role="presentation"
       bgcolor="{BG}" class="bg-body"
       style="background-color:{BG};min-width:100%;">
  <tr>
    <td align="center" bgcolor="{BG}" class="bg-body"
        style="background-color:{BG};padding:32px 16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;"
        valign="top">

      <!-- Content column: max 600px -->
      <table width="600" cellpadding="0" cellspacing="0" role="presentation"
             style="max-width:600px;width:100%;
                    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;">

        <!-- Header -->
        <tr>
          <td style="padding-bottom:24px;">
            <p style="margin:0 0 4px;font-size:11px;text-transform:uppercase;
                      letter-spacing:2px;color:{TEAL};font-weight:700;"
               class="t-teal">Firechicken Solutions</p>
            <p style="margin:0;font-size:26px;font-weight:800;color:{TEXT};
                      letter-spacing:-0.5px;" class="t-primary">Pathfinder</p>
          </td>
        </tr>

        {funnel_html}
        {yes_section}
        {maybe_section}
        {no_section}

        <!-- Footer -->
        <tr>
          <td style="padding-top:24px;border-top:1px solid {BORDER};
                     font-size:13px;color:{MUTED};line-height:1.8;"
              class="t-muted border-footer">
            Scored by AI against your profile &middot; Pathfinder by Firechicken Solutions
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>

</body>
</html>"""

    return subject, html


def send_email(subject: str, html: str):
    sender     = os.getenv("GMAIL_SENDER")
    password   = os.getenv("GMAIL_APP_PASSWORD")
    recipient  = os.getenv("DIGEST_RECIPIENT") or sender

    if not sender or not password:
        print("[Email] GMAIL_SENDER or GMAIL_APP_PASSWORD not set. Skipping.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        print(f"[Email] Digest sent to {recipient}")
    except smtplib.SMTPAuthenticationError:
        print("[Email] ERROR: Authentication failed — check GMAIL_SENDER and GMAIL_APP_PASSWORD.")
    except Exception as e:
        print(f"[Email] ERROR: Failed to send digest — {e}")


def print_digest(jobs: list[dict], metrics: dict):
    yes_jobs   = [j for j in jobs if j["score"] == "YES"]
    maybe_jobs = [j for j in jobs if j["score"] == "MAYBE"]
    no_jobs    = [j for j in jobs if j["score"] == "NO"]

    print(f"\n{'='*60}")
    print(f"  PATHFINDER — {datetime.now().strftime('%B %d, %Y')}")
    print(f"{'='*60}")

    print(f"\n  FUNNEL")
    print(f"  {metrics.get('raw_scraped', 0)} scraped")
    print(f"  {metrics.get('already_seen', 0)} already seen")
    print(f"  {metrics.get('excluded', 0)} excluded by keyword filter")
    print(f"  {metrics.get('url_dedup', 0)} duplicate URL")
    ai_dropped = metrics.get('after_dedup', 0) - metrics.get('after_ai_filter', len(jobs))
    print(f"  {ai_dropped} dropped by AI relevance filter")
    print(f"  {metrics.get('after_ai_filter', len(jobs))} scored  →  {len(yes_jobs)} yes  /  {len(maybe_jobs)} maybe  /  {len(no_jobs)} no")
    print()

    if yes_jobs:
        print(f"\n  QUALIFY ({len(yes_jobs)})\n")
        for j in yes_jobs:
            print(f"  {j['company']} — {j['title']}")
            print(f"  {j['location']}")
            print(f"  {j['reason']}")
            if j.get("hypothesis_category"):
                print(f"  [{j['hypothesis_category']}] {j['hypothesis_signal']}")
            print(f"  {j['url']}\n")

    if maybe_jobs:
        print(f"\n  WORTH A LOOK ({len(maybe_jobs)})\n")
        for j in maybe_jobs:
            print(f"  {j['company']} — {j['title']}")
            print(f"  {j['location']}")
            print(f"  {j['reason']}")
            print(f"  {j['url']}\n")

    print(f"{'='*60}\n")


# ── Salesforce push ───────────────────────────────────────────────────────────

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
        print("[Salesforce] simple-salesforce not installed — skipping.")
        return

    try:
        sf = Salesforce(username=sf_user, password=sf_pass, security_token=sf_token)
    except Exception as e:
        print(f"[Salesforce] Connection failed — {e}")
        return

    from datetime import timedelta
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
            print(f"[Salesforce] Failed — {job['company']}: {job['title']} — {e}")

    print(f"[Salesforce] {pushed} new opportunities pushed, {skipped} already in pipeline.")


# ── Main ──────────────────────────────────────────────────────────────────────

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
        print("\n[Pathfinder] PREVIEW MODE — sending sample email...")
        sample_jobs = [
            {"title": "Salesforce Implementation Consultant", "company": "Acme Consulting",
             "location": "Remote — Canada", "url": "https://linkedin.com/jobs/view/123",
             "score": "YES", "reason": "Full lifecycle Sales Cloud delivery, SMB focus, remote-friendly — strong match.",
             "hypothesis_category": "New capability",
             "hypothesis_signal": "Role is titled 'founding consultant' and reports directly to the VP of Delivery — this seat is building the practice, not backfilling it. Candidate's self-implementation background is the exact proof point.",
             "ghost_detection": "Verified"},
            {"title": "Salesforce Solutions Consultant", "company": "CloudCo",
             "location": "Vancouver, BC (Hybrid)", "url": "https://linkedin.com/jobs/view/456",
             "score": "YES", "reason": "Agentforce implementation valued, declarative-only, mid-market clients.",
             "hypothesis_category": "Capacity",
             "hypothesis_signal": "Three open roles posted in the same month suggests a pipeline problem, not a single gap. Candidate adds immediate delivery capacity without a ramp period.",
             "ghost_detection": "Ghost Likely"},
            {"title": "CRM Implementation Consultant", "company": "Ridge Partners",
             "location": "Remote — Canada", "url": "https://linkedin.com/jobs/view/789",
             "score": "MAYBE", "reason": "Platform not specified — could be Salesforce, could be HubSpot.",
             "hypothesis_category": "Unclear",
             "hypothesis_signal": "Posting uses generic CRM language throughout with no platform named — either intentionally platform-agnostic or written by someone outside the team. Worth a quick look at their tech stack before applying.",
             "ghost_detection": "Unverified"},
            {"title": "Salesforce Admin", "company": "BuildCorp",
             "location": "Remote — Canada", "url": "https://linkedin.com/jobs/view/101",
             "score": "MAYBE", "reason": "Admin-level scope, but admin + consulting hybrid is common at this size.",
             "hypothesis_category": "Backfill",
             "hypothesis_signal": "Single headcount, no growth language — this is a backfill. Low churn risk for candidate.",
             "ghost_detection": "Low Risk"},
            {"title": "Salesforce Functional Consultant", "company": "NorthPeak Group",
             "location": "Remote — Canada", "url": "https://linkedin.com/jobs/view/202",
             "score": "MAYBE", "reason": "Role scope aligns but platform stack is ambiguous — follow up required.",
             "hypothesis_category": "Unclear",
             "hypothesis_signal": "No tech stack named and the role is listed under both IT and Sales divisions — likely an internal headcount debate still in progress.",
             "ghost_detection": "clean"},
        ]
        sample_metrics = {
            "raw_scraped": 87, "already_seen": 12, "excluded": 5,
            "after_dedup": 70, "after_ai_filter": 18,
        }
        subject, html = build_html(sample_jobs, sample_metrics)
        send_email(subject, html)
        print("[Pathfinder] Preview email sent.")
        return

    if args.test:
        print("\n[Pathfinder] TEST MODE — 2 queries · 5 results each · skip AI filter · score top 5 · email sent")
    else:
        print("\n[Pathfinder] Starting...")

    if not os.getenv("GROQ_API_KEY"):
        print("[Pathfinder] ERROR: GROQ_API_KEY is not set. Cannot score jobs.")
        sys.exit(1)

    # 0a. Reply parser — process inbound feedback before anything else so
    #     ghost overrides are live for both cleanup and scoring this run.
    reply_stats = parse_replies()
    if reply_stats["emails_read"]:
        print(
            f"[Reply] {reply_stats['emails_read']} feedback email(s) read, "
            f"{reply_stats['overrides_set']} ghost override(s) written"
        )

    # 0b. Cache cleanup — enforce retention rules before any work begins
    cleanup = run_cache_cleanup()
    total_cleaned = sum(cleanup.values())
    if total_cleaned:
        print(
            f"[Cache] Cleanup: "
            f"{cleanup['expired_companies']} company records expired (90d inactivity)  "
            f"{cleanup['trimmed_repost_entries']} repost entries trimmed (10-entry cap)  "
            f"{cleanup['expired_career_cache']} career page cache entries expired"
        )
    else:
        print("[Cache] Cleanup: nothing to remove")

    # 1. Scout — search + optional AI filter
    print("[Pathfinder] Searching...")
    metrics = {}
    if args.test:
        jobs = scout_jobs(_metrics=metrics, max_queries=2, max_per_query_override=5, skip_ai_filter=True)
    else:
        jobs = scout_jobs(_metrics=metrics)

    from collections import Counter
    source_counts = Counter(j.source or "unknown" for j in jobs)
    print(f"[Pathfinder] {len(jobs)} new listings to score — by source: {dict(source_counts)}")

    if not jobs:
        print("[Pathfinder] No new listings found.")
        return

    # 2. Score — cap at 5 in test mode to limit token usage
    jobs_to_score = jobs[:5] if args.test else jobs
    if args.test and len(jobs) > 5:
        print(f"[Pathfinder] Scoring top 5 of {len(jobs)} (test mode cap)")

    scored = score_all(jobs_to_score)
    metrics["scored_yes"]   = sum(1 for j in scored if j["score"] == "YES")
    metrics["scored_maybe"] = sum(1 for j in scored if j["score"] == "MAYBE")
    metrics["scored_no"]    = sum(1 for j in scored if j["score"] == "NO")
    relevant = [j for j in scored if j["score"] in ("YES", "MAYBE")]

    # Ghost detection — run only on QUALIFY and NEUTRAL results
    print(f"[Ghost] Running ghost detection on {len(relevant)} relevant listings...")
    for job in relevant:
        result = detect_ghost(job)
        job["ghost_detection"] = result
        if result != "clean":
            print(f"  [{result}] {job['company']}: {job['title']}")
    # Ensure NO-scored jobs have the key set to avoid KeyError in templates
    for job in scored:
        job.setdefault("ghost_detection", "clean")

    if not relevant:
        print("[Pathfinder] Nothing relevant this run.")
        print(f"[Pathfinder] Funnel: {metrics.get('raw_scraped',0)} scraped → {len(jobs_to_score)} scored → 0 relevant")
        return

    # 3. Output — always print; also send email (test mode sends the real HTML for rendering checks)
    print_digest(scored, metrics)
    subject, html = build_html(scored, metrics)
    if subject:
        send_email(subject, html)

    # 4. Push YES + MAYBE to Salesforce Career Pipeline
    push_to_salesforce(scored)

    print(f"[Pathfinder] Done. {len([j for j in relevant if j['score']=='YES'])} strong fits, "
          f"{len([j for j in relevant if j['score']=='MAYBE'])} maybes.")


if __name__ == "__main__":
    main()
