"""Job scoring — loads config, builds the LLM prompt, and scores listings."""

import logging
import os

import yaml

from .llm_client import get_llm_response
from .models import JobListing, ScoringResult
from .tracker import get_company_posting_context

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

    qualify_signals    = scoring.get("qualify_signals", {})
    qualify_core       = qualify_signals.get("core", [])
    qualify_strong     = qualify_signals.get("strong", [])
    qualify_supporting = qualify_signals.get("supporting", [])

    neutral_signals   = scoring.get("neutral_signals", {})
    neutral_tradeoffs = neutral_signals.get("acceptable_tradeoffs", [])
    neutral_interp    = neutral_signals.get("interpretation_rule", [])

    disqualify_signals = scoring.get("disqualify_signals", {})
    disqualify_hard    = disqualify_signals.get("hard", [])
    disqualify_exp     = disqualify_signals.get("experience_mismatch", [])
    disqualify_domain  = disqualify_signals.get("domain_lockout", [])

    decision_framework = scoring.get("decision_framework", {})
    framework_rules    = decision_framework.get("rules", [])

    evidence_req     = scoring.get("evidence_requirements", {})
    evidence_extract = evidence_req.get("must_extract", [])
    evidence_rules   = evidence_req.get("rules", [])

    def fmt(lst):
        return "\n".join(f"- {i}" for i in lst)

    highlights_str         = fmt(highlights)
    certs_held_str         = ", ".join(certs_held)
    certs_progress_str     = ", ".join(certs_progress)
    languages_str          = ", ".join(languages)
    hybrid_str             = ", ".join(hybrid_ok)
    qualify_core_str       = fmt(qualify_core)
    qualify_strong_str     = fmt(qualify_strong)
    qualify_supporting_str = fmt(qualify_supporting)
    neutral_tradeoffs_str  = fmt(neutral_tradeoffs)
    neutral_interp_str     = fmt(neutral_interp)
    disqualify_hard_str    = fmt(disqualify_hard)
    disqualify_exp_str     = fmt(disqualify_exp)
    disqualify_domain_str  = fmt(disqualify_domain)
    framework_rules_str    = fmt(framework_rules)
    evidence_extract_str   = fmt(evidence_extract)
    evidence_rules_str     = fmt(evidence_rules)

    return f"""
You are evaluating a job posting for this specific candidate:

CANDIDATE: {name} - {framing}
{highlights_str}
- Certifications held: {certs_held_str}
- Currently completing: {certs_progress_str}
- Languages: {languages_str}
- Location: {base_location} - open to remote or {hybrid_str} hybrid

JOB TO EVALUATE:
Title: {{title}}
Company: {{company}}
Location: {{location}}
Salary: {{salary}}
Description: {{description}}
{{company_context}}
─── DECISION FRAMEWORK ──────────────────────────────────────

Apply signals in this order:
{framework_rules_str}

CONFIDENCE levels:
- HIGH: decision is unambiguous with strong direct evidence
- MEDIUM: decision is clear but some signals are soft or inferred
- LOW: limited information or significant uncertainty in the posting

─── QUALIFY SIGNALS ─────────────────────────────────────────

Core (highest weight - these drive YES):
{qualify_core_str}

Strong (significant weight):
{qualify_strong_str}

Supporting (lower weight - tiebreakers only):
{qualify_supporting_str}

─── NEUTRAL SIGNALS ─────────────────────────────────────────

Acceptable tradeoffs (reduce confidence, do not block YES):
{neutral_tradeoffs_str}

Interpretation:
{neutral_interp_str}

─── DISQUALIFY SIGNALS ──────────────────────────────────────

Hard (any one = NO immediately, no exceptions):
{disqualify_hard_str}

Experience mismatch (any one = NO):
{disqualify_exp_str}

Domain lockout (any one = NO):
{disqualify_domain_str}

─── LOCATION RULE ───────────────────────────────────────────

Score location OK if: fully remote, remote-first, or remote-friendly anywhere in Canada/globally.
Score location OK if: hybrid or in-office in {hybrid_str}.
Score location NO if: hybrid or in-office in any city outside {hybrid_str} with no remote option stated.
If location is unstated or ambiguous, assume remote is possible (location OK).
A location NO is a hard disqualifier.

─── EVIDENCE REQUIREMENTS ───────────────────────────────────

Extract from the job description:
{evidence_extract_str}

Rules:
{evidence_rules_str}

─── HYPOTHESIS CATEGORIES (for YES / MAYBE only) ────────────

Pick exactly one; use Unclear only if no category fits at all.

  Backfill       - Someone left and the role is being refilled. Signals: single headcount, no growth
                   language, role description reads like a job that has existed before, no mention of
                   building or expanding.
  Capacity       - Team is growing under an existing mandate. Signals: multiple open roles at the same
                   company, explicit growth language ("expanding", "scaling", "growing team"), or
                   headcount increase without a new direction.
  New capability - Hiring for something the org doesn't currently have. Signals: "first", "build from
                   scratch", "establish", "create", new platform or product named, reports to exec,
                   founding role language.
  Recovery       - A prior implementation failed or stalled and they need to fix it. Signals: "improve",
                   "optimize", "rescue", "take over", "inherited", implicit or explicit mention of
                   existing system problems, re-implementation language.
  Strategic bet  - Org is making a deliberate platform or business shift. Signals: new product launch,
                   acquisition integration, platform migration, AI/Agentforce initiative, explicit
                   strategic priority language.
  Unclear        - Posting lacks enough signal to distinguish between categories. Use only when none
                   of the above fit - not as a default when evidence is thin.

─── OUTPUT FORMAT ───────────────────────────────────────────

Respond with a single JSON object matching this schema exactly. No prose, no code
fence, no trailing commentary.

{{{{
  "decision": "YES" | "MAYBE" | "NO",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "reasoning": "Under 40 words. State the key signal and how it aligns or conflicts with the candidate's background.",
  "top_qualifier": "The single strongest qualifying signal present, or NONE.",
  "disqualifier": "The triggered disqualifier, or NONE.",
  "evidence": "One verbatim quote from the job description supporting the decision.",
  "hypothesis_category": "One of: Backfill, Capacity, New capability, Recovery, Strategic bet, Unclear. Empty string when decision is NO.",
  "hypothesis_why": "One sentence. Why the company is hiring for this role right now. Required when decision is YES or MAYBE, empty string otherwise.",
  "hypothesis_value": "One sentence. The specific value the candidate brings to the challenge this hire exists to solve. Required when decision is YES or MAYBE, empty string otherwise."
}}}}

Do not use em dashes in any string value. Use a plain hyphen (-) instead.
"""


def _format_company_context(ctx: dict) -> str:
    """Format DB-derived posting history into a prompt section. Returns '' when no signal."""
    lines = []
    if ctx.get("role_repost_count", 0) > 0:
        count = ctx["role_repost_count"]
        times = "once" if count == 1 else f"{count} times"
        lines.append(
            f"- This company has posted a similar role {times} before "
            f"(repost signal - suggests Backfill or Recovery)."
        )
    if ctx.get("company_open_roles", 0) > 1:
        lines.append(
            f"- This company has {ctx['company_open_roles']} distinct active roles in the dataset "
            f"(multiple open headcount - suggests Capacity)."
        )
    if not lines:
        return ""
    return (
        "\nCOMPANY POSTING HISTORY (use alongside posting signals to support or refine the "
        "hypothesis category):\n" + "\n".join(lines) + "\n"
    )


CONFIG = load_config()
SCORING_PROMPT_TEMPLATE = build_scoring_prompt(CONFIG)


def score_job(job: JobListing, company_context: str = "") -> tuple[str, str, str, str, str, str]:
    """Score a single job.

    Returns (score, reason, confidence, hypothesis_category, hypothesis_why, hypothesis_value).

    Raises on schema validation or JSON parse failure so prompt drift surfaces loudly.
    Network-level errors (requests failures, Groq rate limits) are still softened into a
    MAYBE so a transient outage does not halt the whole scoring loop.
    """
    import requests as _requests

    from .llm_client import GroqRateLimitError

    prompt = SCORING_PROMPT_TEMPLATE.format(
        title=job.title,
        company=job.company,
        location=job.location,
        salary=job.salary or "not specified",
        description=(job.description or "")[:3000],
        company_context=company_context,
    )

    try:
        raw = get_llm_response(prompt, max_tokens=500, json_mode=True)
    except (_requests.RequestException, GroqRateLimitError) as e:
        return "MAYBE", f"Could not score - review manually ({e})", "", "", "", ""

    result = ScoringResult.model_validate_json(raw)

    if result.disqualifier and result.disqualifier.upper() != "NONE":
        reason = f"{result.reasoning} Disqualifier: {result.disqualifier}."
    elif result.top_qualifier and result.top_qualifier.upper() != "NONE":
        reason = f"{result.reasoning} Key signal: {result.top_qualifier}."
    else:
        reason = result.reasoning

    return (
        result.decision,
        reason,
        result.confidence,
        result.hypothesis_category,
        result.hypothesis_why,
        result.hypothesis_value,
    )


def score_all(jobs: list[JobListing]) -> list[dict]:
    """Score all jobs and return enriched dicts."""
    results = []
    logger.info(f"[Score] Scoring {len(jobs)} listings...")
    for job in jobs:
        ctx = get_company_posting_context(job.company, job.title, job.url)
        company_context = _format_company_context(ctx)
        score, reason, confidence, hyp_category, hyp_why, hyp_value = score_job(job, company_context)
        icon = "YES" if score == "YES" else ("~~" if score == "MAYBE" else "NO")
        desc_len = len(job.description or "")
        logger.info(f"  [{icon}] {job.company}: {job.title} (desc: {desc_len} chars)")
        logger.info(f"       [{confidence}] {reason}")
        if hyp_category:
            logger.info(f"       [{hyp_category}] {hyp_why} / {hyp_value}")
        results.append({
            "title":               job.title,
            "company":             job.company,
            "location":            job.location,
            "url":                 job.url,
            "source":              job.source,
            "date_posted":         job.date_posted,
            "score":               score,
            "confidence":          confidence,
            "reason":              reason,
            "hypothesis_category": hyp_category,
            "hypothesis_why":      hyp_why,
            "hypothesis_value":    hyp_value,
        })
    return results
