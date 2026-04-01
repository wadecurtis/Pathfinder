"""Email digest builder and sender, plus terminal summary printer."""

import logging
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def build_no_results_email(metrics: dict) -> tuple[str, str]:
    """Build a minimal digest email for runs where no new listings were found."""
    BG     = "#F0F4F8"
    CARD   = "#FFFFFF"
    TEXT   = "#0D2F4F"
    MUTED  = "#64748B"
    TEAL   = "#1D9E75"
    BORDER = "#CBD5E1"

    date_str = datetime.now().strftime("%B %d, %Y")
    subject  = f"Pathfinder - no new listings ({date_str})"

    raw          = metrics.get("raw_scraped", 0)
    already_seen = metrics.get("already_seen", 0)
    excluded     = metrics.get("excluded", 0)
    url_dedup    = metrics.get("url_dedup", 0)
    after_dedup  = metrics.get("after_dedup", 0)
    scored       = metrics.get("after_ai_filter", 0)
    ai_dropped   = max(after_dedup - scored, 0)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="margin:0;padding:0;background-color:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;" bgcolor="{BG}">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" bgcolor="{BG}" style="background-color:{BG};min-width:100%;">
  <tr>
    <td align="center" bgcolor="{BG}" style="background-color:{BG};padding:32px 16px;" valign="top">
      <table width="600" cellpadding="0" cellspacing="0" role="presentation" style="max-width:600px;width:100%;">
        <tr>
          <td style="padding-bottom:24px;">
            <p style="margin:0 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:2px;color:{TEAL};font-weight:700;">Firechicken Solutions</p>
            <p style="margin:0;font-size:26px;font-weight:800;color:{TEXT};letter-spacing:-0.5px;">Pathfinder</p>
          </td>
        </tr>
        <tr>
          <td style="padding-bottom:24px;">
            <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
              <tr>
                <td bgcolor="{CARD}" style="background-color:{CARD};border-radius:10px;padding:18px 20px;border:1px solid {BORDER};">
                  <p style="margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:1.5px;color:{MUTED};font-weight:600;">Pipeline - {date_str}</p>
                  <p style="margin:0 0 12px;font-size:14px;color:{MUTED};line-height:1.9;">
                    {raw}&nbsp;scraped &nbsp;&middot;&nbsp;
                    {already_seen}&nbsp;already seen &nbsp;&middot;&nbsp;
                    {excluded}&nbsp;excluded &nbsp;&middot;&nbsp;
                    {url_dedup}&nbsp;duplicate URL &nbsp;&middot;&nbsp;
                    {ai_dropped}&nbsp;AI filtered
                  </p>
                  <p style="margin:0;font-size:14px;color:{MUTED};">No new listings found this run.</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding-top:24px;border-top:1px solid {BORDER};font-size:13px;color:{MUTED};line-height:1.8;">
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
    subject  = f"Pathfinder - {len(yes_jobs)} strong fits, {len(maybe_jobs)} maybes ({date_str})"

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
             class="t-muted">Pipeline - {date_str}</p>
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
        "Low Risk":     ("#166534", "#DCFCE7"),   # green text on green-50 — old posting, no repost history
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
        if job.get("hypothesis_category") and job.get("hypothesis_why"):
            ghost_note_html = ""
            if job.get("ghost_note"):
                ghost_note_html = (
                    f'<p style="margin:6px 0 0;font-size:14px;color:{MUTED};'
                    f'font-style:italic;line-height:1.5;" class="t-muted">'
                    f'{job["ghost_note"]}</p>'
                )
            hypothesis_html = f"""
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
                 style="margin:0 0 16px;">
            <tr>
              <td class="bg-hypothesis"
                  style="padding:10px 12px;background-color:#F0F9F5;border-radius:6px;
                         border-left:3px solid {TEAL};">
                <p style="margin:0 0 6px;font-size:12px;text-transform:uppercase;
                           letter-spacing:1.2px;color:{TEAL};font-weight:700;"
                   class="t-teal">Hypothesis &middot; {job['hypothesis_category']}</p>
                <p style="margin:0 0 4px;font-size:15px;color:{TEXT};line-height:1.5;"
                   class="t-primary"><strong>Why they're hiring:</strong> {job['hypothesis_why'].replace('—', '-')}</p>
                <p style="margin:0;font-size:15px;color:{TEXT};line-height:1.5;"
                   class="t-primary"><strong>What you bring:</strong> {job['hypothesis_value'].replace('—', '-')}</p>
                {ghost_note_html}
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

        careers_url = job.get("careers_page_url")
        if careers_url:
            careers_line = (
                f'<p style="margin:8px 0 0;font-size:15px;">'
                f'<a href="{careers_url}" style="color:{TEAL};text-decoration:none;">'
                f'Check Careers Page &#8594;</a></p>'
            )
        else:
            careers_line = (
                '<p style="margin:8px 0 0;font-size:15px;font-weight:500;'
                'color:#DC2626;">No careers page found - possible ghost.</p>'
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
             class="t-muted">{job['location'].replace('—', '-')}</p>
          <p style="margin:0 0 12px;font-size:15px;font-weight:500;color:#111111;font-style:italic;line-height:1.5;"
             class="t-reason">{job['reason'].replace('—', '-')}</p>
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
          {careers_line}
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
  <td style="padding:9px 16px;{border}font-size:13px;color:{MUTED};line-height:1.4;" class="t-muted">
    <span style="font-weight:600;color:{TEXT};" class="t-primary">{job['title']} &middot; {job['company']}</span>
    &mdash; {job['reason'].replace('—', '-')}
    <br><a href="{job['url']}" style="font-size:12px;color:{MUTED};text-decoration:underline;" class="t-muted">View posting</a>
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
    sender    = os.getenv("GMAIL_SENDER")
    password  = os.getenv("GMAIL_APP_PASSWORD")
    recipient = os.getenv("DIGEST_RECIPIENT") or sender

    if not sender or not password:
        logger.warning("[Email] GMAIL_SENDER or GMAIL_APP_PASSWORD not set. Skipping.")
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
        logger.info(f"[Email] Digest sent to {recipient}")
    except smtplib.SMTPAuthenticationError:
        logger.error("[Email] ERROR: Authentication failed — check GMAIL_SENDER and GMAIL_APP_PASSWORD.")
    except Exception as e:
        logger.error(f"[Email] ERROR: Failed to send digest — {e}")


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
                print(f"  [{j['hypothesis_category']}] Why: {j.get('hypothesis_why','')} / Value: {j.get('hypothesis_value','')}")
            print(f"  {j['url']}\n")

    if maybe_jobs:
        print(f"\n  WORTH A LOOK ({len(maybe_jobs)})\n")
        for j in maybe_jobs:
            print(f"  {j['company']} — {j['title']}")
            print(f"  {j['location']}")
            print(f"  {j['reason']}")
            print(f"  {j['url']}\n")

    print(f"{'='*60}\n")
