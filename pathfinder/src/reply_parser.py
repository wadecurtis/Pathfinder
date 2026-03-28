"""
Email reply parser — reads Gmail inbox for ghost detection feedback.

Looks for unread replies to Pathfinder digest emails (subject "Re: Pathfinder —").
For each reply, extracts company name mentions and classifies them as:
  - confirmed_real  → user is saying the ghost detection was a false positive
  - confirmed_ghost → user is saying the ghost detection missed a ghost (false negative)

Writes results to the companies.ghost_override column via tracker helpers.
Marks processed emails as read so they are not re-processed on the next run.

Required env vars (same credentials used for outbound email):
  GMAIL_SENDER        — the Gmail address that sends and receives Pathfinder digests
  GMAIL_APP_PASSWORD  — Gmail App Password (not the account password)

If either var is absent the parser skips silently, matching the Salesforce behaviour.
"""

import email
import email.header
import imaplib
import logging
import os
import re

logger = logging.getLogger(__name__)

# ── Preview-mode exclusions ───────────────────────────────────────────────────
# These are the dummy companies used in `pathfinder.py --preview`. They never
# enter job_cache, but are listed here explicitly so that if a real company
# with the same name ever appears in live searches its override cannot be
# written by accident during a reply against sample data.
_PREVIEW_COMPANIES: frozenset[str] = frozenset({
    "acme consulting",
    "cloudco",
    "ridge partners",
    "buildcorp",
    "northpeak group",
})

# ── Classification patterns ───────────────────────────────────────────────────
# Applied to the sentence(s) immediately surrounding a company name mention.
# "confirmed_real" patterns are tested first so "not a ghost" beats bare "ghost".

_REAL_RE = re.compile(
    r"\b(?:"
    r"not\s+a?\s*ghost"
    r"|confirmed[\s_-]+real"
    r"|false[\s_-]+positive"
    r"|actually\s+(?:real|live|hiring|open|active)"
    r"|real\s+(?:job|role|posting|position)"
    r"|legit(?:imate)?"
    r"|is\s+(?:real|live|open|active|hiring)"
    r"|was\s+(?:real|live|open|active|hiring)"
    r"|still\s+(?:hiring|open|active)"
    r"|verified\s+(?:role|job|posting)"
    r")\b",
    re.IGNORECASE,
)

_GHOST_RE = re.compile(
    r"\b(?:"
    r"confirmed[\s_-]+ghost"
    r"|false[\s_-]+negative"
    r"|not\s+(?:hiring|open|active|real)"
    r"|no\s+longer\s+(?:hiring|open|active|available|posted)"
    r"|ghost\s+(?:job|role|posting|position)"
    r"|phantom\s+(?:job|role|posting|position)"
    r"|fake\s+(?:job|role|posting|position)"
    r")\b",
    re.IGNORECASE,
)

# Bare "ghost" without a preceding negation — weaker signal, checked last
_BARE_GHOST_RE = re.compile(r"\bghost\b", re.IGNORECASE)
_NEGATION_GHOST_RE = re.compile(r"\bnot\s+(?:\w+\s+)?ghost\b", re.IGNORECASE)


def _classify_context(text: str) -> str | None:
    """
    Return 'confirmed_real', 'confirmed_ghost', or None for a text snippet.
    text should be the sentence(s) surrounding the company name mention.
    """
    if _REAL_RE.search(text):
        return "confirmed_real"
    if _GHOST_RE.search(text):
        return "confirmed_ghost"
    # Bare "ghost" only counts if it is not negated in the same text
    if _BARE_GHOST_RE.search(text) and not _NEGATION_GHOST_RE.search(text):
        return "confirmed_ghost"
    return None


def _extract_plain_text(msg: email.message.Message) -> str:
    """Walk a parsed email message and return the plain-text body."""
    text_parts = []
    for part in msg.walk():
        ct = part.get_content_type()
        cd = part.get("Content-Disposition", "")
        if ct == "text/plain" and "attachment" not in cd:
            charset = part.get_content_charset() or "utf-8"
            try:
                text_parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
            except Exception:
                pass
    return "\n".join(text_parts)


def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentence-like chunks. Splits on line breaks, periods,
    exclamation marks, and question marks while keeping each chunk non-empty.
    """
    chunks = re.split(r"[\n\r.!?]+", text)
    return [c.strip() for c in chunks if c.strip()]


def _extract_overrides(body: str, known_companies: list[str]) -> list[tuple[str, str]]:
    """
    Scan the reply body for company name mentions and classify each as an override.

    Args:
        body:             Plain-text email body.
        known_companies:  List of company names from job_cache (canonical spellings).

    Returns:
        List of (canonical_company_name, override_value) pairs where override_value
        is 'confirmed_real' or 'confirmed_ghost'.
    """
    body_lower = body.lower()
    sentences  = _split_sentences(body)
    results    = []
    seen       = set()  # avoid duplicate entries for the same company per email

    for company in known_companies:
        if company.strip().lower() in _PREVIEW_COMPANIES:
            continue
        name_lower = company.strip().lower()
        if not name_lower or name_lower in seen:
            continue
        if name_lower not in body_lower:
            continue

        # Collect all sentences that mention this company
        context_sentences = [s for s in sentences if name_lower in s.lower()]
        if not context_sentences:
            continue

        # Classify combined context (multiple sentences can reinforce the signal)
        combined = " ".join(context_sentences)
        override = _classify_context(combined)
        if override:
            results.append((company, override))
            seen.add(name_lower)
            logger.debug(f"Reply override detected: {company!r} → {override}")

    return results


def _decode_header_value(raw: str) -> str:
    """Decode an RFC 2047-encoded email header value to a plain string."""
    parts = email.header.decode_header(raw or "")
    decoded = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(chunk)
    return "".join(decoded)


# ── Public entry point ────────────────────────────────────────────────────────

def parse_replies() -> dict:
    """
    Connect to Gmail via IMAP and process unread Pathfinder reply emails.

    For each matching reply, extracts ghost detection overrides and writes
    them to the companies table.  Marks each processed email as read.

    Returns:
        {
          "emails_read":   int,   # unread Pathfinder replies found
          "overrides_set": int,   # ghost override records written
        }

    Silently returns zero-counts if GMAIL_SENDER or GMAIL_APP_PASSWORD are unset.
    """
    stats = {"emails_read": 0, "overrides_set": 0}

    sender   = os.getenv("GMAIL_SENDER")
    password = os.getenv("GMAIL_APP_PASSWORD")
    if not sender or not password:
        return stats

    # Load canonical company names once — used for matching across all emails
    from .tracker import get_cached_companies, set_ghost_override
    known_companies = get_cached_companies()
    if not known_companies:
        logger.debug("Reply parser: no cached companies to match against — skipping")
        return stats

    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap.login(sender, password)
    except imaplib.IMAP4.error as exc:
        logger.warning(f"[Reply] IMAP login failed — {exc}")
        return stats
    except Exception as exc:
        logger.warning(f"[Reply] IMAP connection error — {exc}")
        return stats

    try:
        imap.select("INBOX")

        # Search for unread replies to any Pathfinder digest
        _status, msg_ids_raw = imap.search(None, '(UNSEEN SUBJECT "Re: Pathfinder")')
        msg_ids = (msg_ids_raw[0] or b"").split()

        for msg_id in msg_ids:
            try:
                _f, msg_data = imap.fetch(msg_id, "(RFC822)")
                raw_bytes = msg_data[0][1]
                msg = email.message_from_bytes(raw_bytes)

                subject = _decode_header_value(msg.get("Subject", ""))
                logger.debug(f"[Reply] Processing: {subject!r}")

                body = _extract_plain_text(msg)
                if not body.strip():
                    # Mark read even if empty so we don't re-process next run
                    imap.store(msg_id, "+FLAGS", "\\Seen")
                    continue

                overrides = _extract_overrides(body, known_companies)
                for company, override in overrides:
                    try:
                        set_ghost_override(company, override, source="email_reply")
                        stats["overrides_set"] += 1
                        logger.info(
                            f"[Reply] Override written: {company!r} → {override} "
                            f"(from subject {subject!r})"
                        )
                    except Exception as exc:
                        logger.warning(f"[Reply] Failed to write override for {company!r}: {exc}")

                # Mark as read regardless of whether overrides were found
                imap.store(msg_id, "+FLAGS", "\\Seen")
                stats["emails_read"] += 1

            except Exception as exc:
                logger.warning(f"[Reply] Error processing message {msg_id}: {exc}")
                continue

    finally:
        try:
            imap.logout()
        except Exception:
            pass

    return stats
