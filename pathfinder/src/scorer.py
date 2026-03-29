"""Job scoring — loads config, builds the LLM prompt, and scores listings."""

import logging
import os

import yaml

from .llm_client import get_llm_response
from .models import JobListing

logger = logging.getLogger(__name__)


def load_config() -> dict:
    config_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
    )
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

─── SCORING PHILOSOPHY ───────────────────────────────────────

Job postings describe ideal candidates, not minimum requirements. Treat
stated years of experience, preferred certifications, and seniority titles
as signals to weigh — not hard gates. The only hard gates are items in the
DISQUALIFY list above.

When evaluating experience gaps or missing preferred qualifications, weigh
them against the candidate's demonstrated outcomes in the highlights above.
Strong delivery evidence outweighs years-of-experience requirements. A
candidate who meets 80% of requirements with clear proof of outcomes on the
remaining 20% is a MAYBE, not a NO.

Specific guidance:
- "5+ years required" and candidate has 4 with strong outcomes → weigh, don't gate
- "Consulting firm experience preferred" → preferred is not required; weigh against highlights
- "X certification required" → only a hard gate if listed in DISQUALIFY; otherwise a soft signal
- "Senior" or "Lead" in title → signals scope, not a hard eligibility requirement
- A certification the candidate is actively completing ({certs_progress_str}) → treat as nearly held

─── SCORING RULES ────────────────────────────────────────────

Score YES   — qualify criteria clearly outweigh neutrals and NO disqualifier is present.
Score MAYBE — qualify criteria are present but soft gaps exist (experience, preferred certs,
              seniority title), OR exactly one neutral applies. Use MAYBE when the candidate
              has strong outcome evidence that offsets a stated preference or soft requirement.
Score NO    — any hard disqualifier from the DISQUALIFY list is present, regardless of
              qualify criteria. Do not score NO for soft gaps, preferred requirements,
              or years-of-experience mismatches alone.

LOCATION RULE (apply before scoring):
- Score location OK if: fully remote, remote-first, or remote-friendly anywhere in Canada/globally.
- Score location OK if: hybrid or in-office in {hybrid_str}.
- Score location NO if: hybrid or in-office in any city outside {hybrid_str} with no remote option stated.
- If location is unstated or ambiguous, assume remote is possible (location OK).
A location NO is a hard disqualifier — score the overall job NO.

Do not hedge. Make a call.

Respond in exactly this format:
SCORE: YES / MAYBE / NO
REASON: Two sentences. Sentence 1: name the key qualifier or disqualifier that decided the score. Sentence 2: explain specifically how it aligns with or conflicts with the candidate's background.

If SCORE is YES or MAYBE, also output:
HYPOTHESIS_CATEGORY: pick exactly one using the definitions below — choose the best fit; use Unclear only if no category fits at all.

  Backfill       — Someone left and the role is being refilled. Signals: single headcount, no growth
                   language, role description reads like a job that has existed before, no mention of
                   building or expanding.
  Capacity       — Team is growing under an existing mandate. Signals: multiple open roles at the same
                   company, explicit growth language ("expanding", "scaling", "growing team"), or
                   headcount increase without a new direction.
  New capability — Hiring for something the org doesn't currently have. Signals: "first", "build from
                   scratch", "establish", "create", new platform or product named, reports to exec,
                   founding role language.
  Recovery       — A prior implementation failed or stalled and they need to fix it. Signals: "improve",
                   "optimize", "rescue", "take over", "inherited", implicit or explicit mention of
                   existing system problems, re-implementation language.
  Strategic bet  — Org is making a deliberate platform or business shift. Signals: new product launch,
                   acquisition integration, platform migration, AI/Agentforce initiative, explicit
                   strategic priority language.
  Unclear        — Posting lacks enough signal to distinguish between categories. Use only when none
                   of the above fit — not as a default when evidence is thin.

HYPOTHESIS_SIGNAL: One sentence. Quote or closely paraphrase the specific language from the posting
that supports your category choice.

Do not use em dashes (-) anywhere in your response. Use a plain hyphen (-) instead.
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
        response = get_llm_response(prompt, max_tokens=350)
        score        = "NO"
        reason       = ""
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
        reason     = reason.replace("—", "-")
        hyp_signal = hyp_signal.replace("—", "-")
        return score, reason, hyp_category, hyp_signal
    except Exception as e:
        return "MAYBE", f"Could not score — review manually ({e})", "", ""


def score_all(jobs: list[JobListing]) -> list[dict]:
    """Score all jobs and return enriched dicts."""
    results = []
    logger.info(f"[Score] Scoring {len(jobs)} listings...")
    for job in jobs:
        score, reason, hyp_category, hyp_signal = score_job(job)
        icon = "YES" if score == "YES" else ("~~" if score == "MAYBE" else "NO")
        desc_len = len(job.description or "")
        logger.info(f"  [{icon}] {job.company}: {job.title} (desc: {desc_len} chars)")
        logger.info(f"       {reason}")
        if hyp_category:
            logger.info(f"       [{hyp_category}] {hyp_signal}")
        results.append({
            "title":               job.title,
            "company":             job.company,
            "location":            job.location,
            "url":                 job.url,
            "source":              job.source,
            "date_posted":         job.date_posted,
            "score":               score,
            "reason":              reason,
            "hypothesis_category": hyp_category,
            "hypothesis_signal":   hyp_signal,
        })
    return results
