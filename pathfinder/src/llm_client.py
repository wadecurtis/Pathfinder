"""Unified LLM client supporting both Groq and Anthropic."""

import json
import os

import requests

from .profile_loader import load_settings


# Available Groq models with descriptions for the UI
GROQ_MODELS = {
    "llama-3.3-70b-versatile": {"name": "Llama 3.3 70B", "desc": "Best quality, 6K TPM", "tpm": "6K"},
    "meta-llama/llama-4-scout-17b-16e-instruct": {"name": "Llama 4 Scout 17B", "desc": "Good quality, 30K TPM", "tpm": "30K"},
    "qwen/qwen3-32b": {"name": "Qwen 3 32B", "desc": "Good quality, 6K TPM", "tpm": "6K"},
    "llama-3.1-8b-instant": {"name": "Llama 3.1 8B", "desc": "Fastest, lower quality, 6K TPM", "tpm": "6K"},
}


class GroqRateLimitError(Exception):
    """Raised when Groq rate limit is hit, carrying available model alternatives."""

    def __init__(self, message: str, current_model: str, available_models: list[dict]):
        super().__init__(message)
        self.current_model = current_model
        self.available_models = available_models  # [{"id": ..., "name": ..., "desc": ...}, ...]


# Track which models were used during a generation cycle
_models_used: list[str] = []


def get_models_used() -> list[str]:
    """Return the list of model IDs used since the last reset."""
    return list(_models_used)


def reset_models_used():
    """Clear the model usage tracker (call at the start of a generation cycle)."""
    _models_used.clear()


_GROQ_FALLBACK_MODELS = [
    "meta-llama/llama-4-scout-17b-16e-instruct",  # 30K TPM, 500K TPD
    "qwen/qwen3-32b",                              # 6K TPM, 500K TPD
    "llama-3.1-8b-instant",                         # 6K TPM, 500K TPD
]


def get_llm_response(prompt: str, max_tokens: int = 4096, model_override: str | None = None) -> str:
    """Get a response from the configured LLM provider (Groq or Anthropic).

    If model_override is provided, Groq will use that model instead of the configured default.
    """
    settings = load_settings()
    provider = settings.get("llm", {}).get("provider", "groq")

    if provider == "groq":
        return _groq_response(prompt, max_tokens, settings, model_override=model_override)
    else:
        return _anthropic_response(prompt, max_tokens, settings)


def _groq_response(prompt: str, max_tokens: int, settings: dict, model_override: str | None = None) -> str:
    """Call Groq API (OpenAI-compatible) with retry on rate limits.

    Tries the configured model. If model_override is provided, uses that model with
    automatic fallback through remaining models. If rate limited beyond recovery,
    raises GroqRateLimitError.
    """
    import logging

    logger = logging.getLogger(__name__)

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in environment / .env file")

    primary_model = settings.get("llm", {}).get("groq_model", "llama-3.3-70b-versatile")

    if model_override:
        # model_override provided — try that model with patience, then fallback
        logger.info("Using model override: %s", model_override)
        models_to_try = [model_override] + [m for m in _GROQ_FALLBACK_MODELS if m != model_override]
        if primary_model not in models_to_try:
            models_to_try.append(primary_model)
        patient = True
    else:
        # Default — only try primary model
        models_to_try = [primary_model]
        patient = False

    for model_idx, model in enumerate(models_to_try):
        if model_idx > 0:
            model_name = GROQ_MODELS.get(model, {}).get("name", model)
            logger.warning("Model rate limited. Falling back to: %s", model_name)
        result = _groq_call_with_retry(prompt, max_tokens, api_key, model, logger, patient=patient)
        if result is not None:
            _models_used.append(model)
            return result

    model_name = GROQ_MODELS.get(primary_model, {}).get("name", primary_model)
    raise GroqRateLimitError(
        f"Groq daily rate limit reached for {model_name}.",
        current_model=primary_model,
        available_models=[],
    )


def _groq_call_with_retry(
    prompt: str, max_tokens: int, api_key: str, model: str, logger,
    patient: bool = False,
) -> str | None:
    """Try a single Groq model with retries. Returns None if rate limited beyond recovery.

    If patient=True (user already chose a model), wait up to 5 minutes for rate limits
    instead of giving up after 90s. This ensures all 3 generation calls can complete.
    """
    import time

    max_retries = 3 if not patient else 5
    max_wait = 90 if not patient else 300  # 90s default, 5min when patient

    for attempt in range(max_retries):
        resp = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.7,
            },
            timeout=120,
        )

        # Log rate limit status from headers (always present)
        remaining_req = resp.headers.get("x-ratelimit-remaining-requests", "?")
        remaining_tok = resp.headers.get("x-ratelimit-remaining-tokens", "?")
        limit_req = resp.headers.get("x-ratelimit-limit-requests", "?")
        limit_tok = resp.headers.get("x-ratelimit-limit-tokens", "?")
        logger.info(
            "Groq [%s] — requests: %s/%s remaining, tokens: %s/%s remaining",
            model, remaining_req, limit_req, remaining_tok, limit_tok,
        )

        if resp.status_code == 429:
            # Parse retry-after or use exponential backoff
            retry_after = resp.headers.get("retry-after")
            reset_req = resp.headers.get("x-ratelimit-reset-requests", "")
            reset_tok = resp.headers.get("x-ratelimit-reset-tokens", "")

            if retry_after:
                wait = float(retry_after)
            else:
                wait = min(2 ** attempt * 5, 60)  # 5s, 10s, 20s, 60s

            # Determine which limit was hit
            limit_type = "tokens per minute"
            if remaining_req == "0":
                limit_type = f"daily requests (resets: {reset_req})"
            elif remaining_tok == "0":
                limit_type = f"tokens/min (resets: {reset_tok})"
            elif wait > 120:
                limit_type = "daily token quota"

            # If wait exceeds our patience threshold, give up on this model
            if wait > max_wait:
                logger.warning(
                    "Model %s limit hit (%s). Wait would be %dm %ds (max %ds) — trying fallback.",
                    model, limit_type, int(wait // 60), int(wait % 60), max_wait,
                )
                return None  # Signal to try next model

            logger.warning(
                "Groq rate limited: %s [%s] (attempt %d/%d). Waiting %.0fs...%s",
                limit_type, model, attempt + 1, max_retries, wait,
                " (patient mode)" if patient else "",
            )
            time.sleep(wait)
            continue

        resp.raise_for_status()
        data = resp.json()

        # Log token usage for this call
        usage = data.get("usage", {})
        if usage:
            logger.info(
                "Groq [%s] — prompt: %d, completion: %d, total: %d tokens",
                model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("total_tokens", 0),
            )

        return data["choices"][0]["message"]["content"].strip()

    # All retries exhausted for this model
    return None


def _anthropic_response(prompt: str, max_tokens: int, settings: dict) -> str:
    """Call Anthropic Claude API."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment / .env file")

    model = settings.get("llm", {}).get("anthropic_model", "claude-sonnet-4-20250514")
    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def parse_json_response(text: str) -> dict:
    """Extract and parse JSON from an LLM response that may contain markdown.

    Handles common LLM quirks: markdown fences, trailing commas, unescaped
    newlines inside string values, and truncated output.
    """
    import logging
    import re

    logger = logging.getLogger(__name__)

    text = text.strip()

    # Strip markdown code fences (may appear after preamble text like "Here are the documents:")
    fence_match = re.search(r"```(?:json)?\s*\n([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()
    elif "```" in text:
        # Fence exists but may be unclosed (truncated response) — extract content after first fence
        parts = text.split("```", 2)
        if len(parts) >= 2:
            inner = parts[1]
            # Strip optional language tag on first line
            if inner.startswith("json"):
                inner = inner[4:]
            inner = inner.strip()
            if inner:
                text = inner

    # Fix Llama-style string concatenation: "part 1"\n    + "part 2" → "part 1part 2"
    if '"\n' in text and '+ "' in text:
        text = re.sub(r'"\s*\+\s*"', '', text)

    # Fix raw newlines inside JSON string values — LLMs often put literal newlines
    # instead of \n escape sequences. Walk the text and escape them.
    text = _escape_newlines_in_json_strings(text)

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract the outermost JSON object
    start = text.find("{")
    if start != -1:
        # Find matching closing brace
        depth = 0
        end = -1
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\":
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end != -1:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

            # Fix trailing commas before } or ]
            cleaned = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    # Last resort: if JSON was truncated (LLM hit token limit), try to repair
    # by closing open strings, arrays, and objects
    fragment = text[start:] if start != -1 else text

    # Try progressively aggressive truncation repairs
    min_len = max(start + 10 if start != -1 else 10, 50)
    for attempt in range(8):
        repair = fragment
        # Close any open strings
        if repair.count('"') % 2 == 1:
            repair += '"'
        # Close open arrays and objects
        open_braces = repair.count("{") - repair.count("}")
        open_brackets = repair.count("[") - repair.count("]")
        repair += "]" * max(0, open_brackets) + "}" * max(0, open_braces)
        # Fix trailing commas
        repair = re.sub(r",\s*([}\]])", r"\1", repair)
        try:
            return json.loads(repair)
        except json.JSONDecodeError:
            # Cut at a reasonable boundary — find the last complete key-value pair
            # by looking for '",\n' or '"],\n' or '}\n' patterns
            cut_at = -1
            for pattern in ['\n  "', '",\n', '"],\n', '}\n']:
                pos = fragment.rfind(pattern, min_len, len(fragment) - 20)
                if pos > cut_at:
                    cut_at = pos
            if cut_at <= min_len:
                # Fallback: just chop 20% off the end
                cut_at = int(len(fragment) * 0.8)
                if cut_at <= min_len:
                    break
            fragment = fragment[:cut_at]

    # Final fallback: model returned plain text instead of JSON (e.g., cover letter as prose).
    # Try to extract recognizable sections into a dict.
    result = _extract_plain_text_sections(text)
    if result:
        return result

    logger.error("JSON parse failed. Length: %d, starts with: %r, ends with: %r",
                 len(text), text[:200], text[-200:] if len(text) > 200 else text)
    raise ValueError(f"Could not parse JSON from LLM response. First 500 chars: {text[:500]}")


def _escape_newlines_in_json_strings(text: str) -> str:
    """Escape literal newlines/tabs inside JSON string values.

    LLMs (especially Llama) often put real newline characters inside JSON strings
    instead of \\n escape sequences. This walks the text character by character,
    tracks whether we're inside a quoted string, and replaces raw newlines with \\n.
    """
    result = []
    in_string = False
    i = 0
    while i < len(text):
        c = text[i]
        if c == '\\' and in_string:
            # Escaped character — keep as-is and skip next char
            result.append(c)
            if i + 1 < len(text):
                i += 1
                result.append(text[i])
            i += 1
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
        elif in_string and c == '\n':
            result.append('\\n')
        elif in_string and c == '\t':
            result.append('\\t')
        elif in_string and c == '\r':
            result.append('\\r')
        else:
            result.append(c)
        i += 1
    return ''.join(result)


def _extract_plain_text_sections(text: str) -> dict | None:
    """Extract cover letter, fit summary, and gap analysis from plain text when model ignores JSON.

    Looks for markdown headers or numbered sections like:
      **COVER LETTER** / ## Cover Letter / 1. COVER LETTER
      **FIT SUMMARY** / ## Fit Summary / 2. FIT SUMMARY
      **GAP ANALYSIS** / ## Gap Analysis / 3. GAP ANALYSIS
    """
    import re

    # Patterns to split on — try to find any of these section headers
    section_patterns = [
        r"(?i)(?:\*\*|#{1,3}\s*|(?:\d+[\.\)]\s*))?\s*cover\s*letter\s*(?:\*\*|:?)?\s*\n",
        r"(?i)(?:\*\*|#{1,3}\s*|(?:\d+[\.\)]\s*))?\s*fit\s*summary\s*(?:\*\*|:?)?\s*\n",
        r"(?i)(?:\*\*|#{1,3}\s*|(?:\d+[\.\)]\s*))?\s*gap\s*analysis\s*(?:\*\*|:?)?\s*\n",
    ]

    # Find positions of each section
    positions = []
    for pat in section_patterns:
        m = re.search(pat, text)
        if m:
            positions.append((m.start(), m.end()))

    if not positions:
        return None

    # Sort by position and extract text between headers
    positions.sort(key=lambda x: x[0])
    sections = []
    for i, (start, content_start) in enumerate(positions):
        if i + 1 < len(positions):
            content = text[content_start:positions[i + 1][0]]
        else:
            content = text[content_start:]
        sections.append(content.strip())

    # If we got at least a cover letter, build the result
    if not sections:
        return None

    result = {}
    # First section = cover letter
    result["cover_letter"] = sections[0].strip()

    # Fit summary = second section, split into bullets
    if len(sections) > 1:
        bullets = [b.strip().lstrip("-•*").strip() for b in sections[1].strip().split("\n") if b.strip()]
        result["fit_summary"] = bullets

    # Gap analysis = third section, split into bullets
    if len(sections) > 2:
        bullets = [b.strip().lstrip("-•*").strip() for b in sections[2].strip().split("\n") if b.strip()]
        result["gap_analysis"] = bullets

    return result
