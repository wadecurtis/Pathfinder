# Pathfinder: Build Narrative

---

## Stage 1: Fork and Rename *(commits 1-6)*

**The problem:** The project started from an existing job scraper codebase (`naukri-main`) built for a different market. It needed to be stripped of all prior branding and repurposed from scratch.

**What was built:** The repo was renamed, all references to the original project (`naukri`, `merinaukri`, `sf-jobs`) were scrubbed from the codebase, and the folder structure was cleaned up.

**Key insight:** Starting from a working scraper skeleton saved significant time. The job board integration, deduplication, and seen-jobs tracker were already functional. The work was in making it yours, not rebuilding plumbing.

**Wrong turn:** The workflow file was committed to the wrong path initially (`daily.yml` not inside `.github/workflows/`), so GitHub Actions didn't recognize it. Fixed in the second commit.

---

## Stage 2: Scoring Layer *(commits 7-16)*

**The problem:** Raw job scrapes are noise. The tool needed to evaluate each listing against a specific candidate profile and return a signal, not a list.

**What was built:** An LLM scoring prompt (via Groq) that evaluated each job against three criteria: role fit, experience requirements, and location. Scores were YES / MAYBE / NO with a one-sentence reason.

**Key decisions and iterations:**

- First version hedged too much. Nearly everything came back MAYBE. Fixed by making the prompt more decisive: *"Do not hedge. Make a call."*
- Google Jobs was added as a source, then swapped for Glassdoor after scraping reliability issues
- The `title_keywords` positive filter was intentionally disabled after it was found to be dropping valid Indeed/Glassdoor results that used slightly different title conventions
- LinkedIn descriptions weren't being fetched initially. Jobs were being scored on title and company name only. Fixed by enabling description fetching and raising the content cap to 3,000 chars.
- A Windows-specific Unicode crash (`cp1252` encoding) appeared when scoring output contained non-ASCII characters. Fixed in `score_all()`
- French/bilingual requirement added as a hard disqualifier after it surfaced as a real false-positive category for Canada-based searches

**Key insight:** The scoring prompt is the product. Every other part of the pipeline is commodity infrastructure. Time spent tightening the prompt paid off more than any other change.

---

## Stage 3: Profile Extraction and Email Digest *(commits 17-22)*

**The problem:** The candidate profile was hardcoded inside the scoring prompt, making it painful to update. The output was terminal-only with no email and no shareable artifact.

**What was built:**

- Candidate profile extracted into `profile.yaml`: highlights, certifications, location preferences, full resume data
- HTML email digest built and sent via Gmail SMTP
- Full funnel metrics added to both the email and console output: scraped > already seen > excluded > AI filtered > scored > yes/maybe/no
- Firechicken Solutions branding applied to the email header

**Key insight:** The funnel metrics line was a late addition that turned out to be one of the most valuable features. It makes the pipeline legible at a glance and immediately surfaces when something is wrong (rate limiting, over-filtering, etc.).

---

## Stage 4: Scoring Overhaul - QUALIFY/NEUTRAL/DISQUALIFY *(commit 23)*

**The problem:** The original three-criterion scoring rubric was too generic. It didn't reflect the specific positioning of the candidate (Salesforce practitioner moving into consulting) and was producing too many MAYBEs on roles that should have been hard NOs.

**What was built:** The scoring criteria were completely replaced with an explicit QUALIFY / NEUTRAL / DISQUALIFY framework:

- **QUALIFY:** indicators that push toward YES (full lifecycle delivery, SMB focus, Agentforce valued, etc.)
- **NEUTRAL:** present but not disqualifying (enterprise clients, CRM other than Salesforce, pre-sales direction)
- **DISQUALIFY:** hard NOs regardless of qualify criteria (requires Apex, French required, global SI employer, US-only work auth, salary below floor, etc.)

**Key insight:** The disqualify list is where most of the precision comes from. Specific, concrete rules ("requires Apex or LWC", "employer is Accenture/Deloitte/IBM") produce far sharper results than vague rubric criteria. The LOCATION RULE as a separate named block inside the prompt also helped, as location scoring was previously inconsistent.

---

## Stage 5: Config Consolidation - Single config.yaml *(commit 24)*

**The problem:** User-configurable settings were split across `profile.yaml`, `settings.yaml`, and the scoring prompt in `pathfinder.py`. A new user cloning the repo had to edit three separate files to get started.

**What was built:** All user-facing configuration consolidated into a single `config.yaml` at the repo root with four sections: `profile`, `scoring`, `search`, and `llm`. `pathfinder.py` reads directly from this file. `profile_loader.py` was updated to prefer `config.yaml` and translate its `search:` key into the structure `scout.py` expects, while keeping `profile.yaml`/`settings.yaml` intact for web dashboard compatibility.

**Key insight:** Clone-and-go usability required a single file a new user could open, read top to bottom, and fill in. Two config files with overlapping concerns is a support burden. One file with clear section headers is not.

---

## Stage 6: Beta Readiness and README *(commits 25-32)*

**The problem:** The project worked for the person who built it but wasn't ready for someone else to clone and run independently.

**What was built:**

- `.env.example` created (it was missing; `setup.sh` referenced it but it didn't exist)
- `requirements.txt` trimmed to 7 core dependencies; `requirements.full.txt` created for web dashboard/bot features
- README completely rewritten for a non-technical user: Python install (Windows PATH checkbox called out explicitly), Git install, venv creation with PowerShell execution policy fix, `.env` copy commands, `config.yaml` walkthrough, GitHub token setup with Workflows permission called out, secrets table, cron schedule table
- Stale inner README deleted
- `config.example.yaml` deleted (redundant; `config.yaml` is committed and always present on clone)

**Real errors documented from the first user run:**

- `.env.example` didn't exist when `setup.sh` tried to copy it
- `settings.yaml` was gitignored, so the GitHub Actions runner couldn't find it
- `daily.yml` was placed at repo root instead of `.github/workflows/`
- GitHub token was missing the Workflows permission, causing push rejection
- PowerShell blocked venv activation script by default (execution policy)
- `locations:` required exact strings (`"Canada"`, not `"United States"`)

**Key insight:** The README should be written assuming the reader has never used a terminal. Every step that can fail, will fail for someone. The troubleshooting section should document real errors from real runs, not hypothetical ones.

---

## Stage 7: Email Rendering - Outlook Dark Mode *(commits 33-38)*

**The problem:** The original email used div-based layout with CSS background colors. Outlook (Word rendering engine) ignores CSS `background-color` on divs entirely. It only respects `bgcolor` attributes on `<table>` and `<td>` elements.

**What was built - Round 1:** Full HTML rewrite from div-based to table-based layout. Every background color expressed as both a `bgcolor` attribute and inline `style`. Buttons wrapped in `<table><tr><td bgcolor>`. Left accent bars replaced from `border-left` CSS to a narrow 4px `<td>` column with its own `bgcolor`.

**What happened:** The email rendered as a muddy light blue-gray in Outlook dark mode, not the intended dark navy. Outlook was applying its own color transformation on top of the explicit `bgcolor` attributes.

**What was built - Round 2:** Flipped the entire color approach. Light mode became the default, with all `bgcolor` attributes and inline styles set to a clean light palette (`#F0F4F8` page background, `#FFFFFF` cards, `#0D2F4F` navy text). Dark mode moved into a `@media screen and (prefers-color-scheme: dark)` block for clients that support it (Gmail, Apple Mail, iOS). Outlook Classic ignores media queries entirely and gets the intentional light version.

**Key insight:** Stop fighting Outlook. Design for it. Outlook Classic will always render the default palette, so make the default palette look good. Clients that support `prefers-color-scheme` get the dark version as an enhancement, not a requirement.

**Additional fix:** Font was rendering as serif in Outlook. Root cause: `font-family` was only on the inner 600px content table. Outlook needs it on `<body>` and the outer `<td>` as well.

---

## Stage 8: Test Infrastructure *(commits 39-43)*

**The problem:** Full runs consume API tokens and hit job boards. New users needed a way to validate their config and check email rendering without running the full pipeline.

**What was built:**

- `--test` mode: 2 queries, 5 results per query, AI filter skipped, top 5 scored, terminal output plus real HTML email sent. Designed for config validation and rendering checks on first setup.
- `--preview` mode: no job board queries, no scoring, no tokens. Sends a hardcoded sample email immediately. Designed for checking email rendering in isolation, especially useful when LinkedIn rate-limits after repeated test runs.

**Wrong turn:** `--test` initially only printed to terminal. The email rendering problem (Outlook dark mode) only surfaced after adding email send to test mode. Without sending the actual HTML email, there was no way to catch rendering issues before the first real run.

**Key insight:** Rate limiting is a real constraint for local testing. LinkedIn will throttle after 3-4 runs in quick succession, returning 0 results and making it look like the pipeline is broken. `--preview` sidesteps this entirely.

---

## Stage 9: Scheduling *(commits 44-47)*

**The problem:** The workflow was set to weekdays only (`1-5`). The cron had never fired on its own. All 16 runs in the Actions tab were manual.

**What was built:** Schedule changed to daily (`* * *`). An empty commit was pushed to re-register the cron with GitHub's scheduler (a known requirement after changing schedule configuration). Confirmed working by temporarily setting the cron to fire 2 minutes in the future, observing it trigger, then restoring to 8am PST.

**Key insight:** GitHub's scheduled cron on free accounts requires a push to the default branch to activate or re-activate. It does not pick up changes retroactively. Testing with a near-future time (e.g. current time + 2 minutes) is the fastest way to confirm the schedule is wired correctly without waiting until the next morning.

---

## Stage 10: Bloat Strip and Public Repo Prep *(commits 48-55)*

**The problem:** The repo had accumulated significant dead weight: a Telegram bot, a resume auto-generator, a Flask web dashboard, a network tracker, and associated templates, static files, and config. None of it belonged in a focused job digest tool, and all of it would confuse someone cloning for the first time.

**What was removed:**
- `pathfinder/src/bot/`: Telegram bot (polling, command handlers, message formatting)
- `pathfinder/src/generator/`: resume auto-generation from job description
- `pathfinder/src/web/`: Flask dashboard and API endpoints
- `pathfinder/src/network/`: network contact matcher
- `pathfinder/src/profile_builder.py`, `profile_updater.py`
- `pathfinder/main.py`, `pathfinder/dashboard.py`
- `pathfinder/templates/`, `pathfinder/static/`
- `pathfinder/requirements.full.txt`: full dependency list for removed features
- `pathfinder/config/`: legacy config directory with stale YAML fragments

**What was stripped from surviving files:**
- `models.py`: cut to `JobListing` only (removed UserProfile, Experience, Education, GeneratedResume, NetworkMatch, Application)
- `tracker.py`: cut to seen_jobs dedup and job_cache only (removed applications CRUD, network_cache, resume_cache, cover_letter_cache)
- `profile_loader.py`: cut to `load_settings()` only (removed `load_profile()`, legacy settings.yaml fallback)
- `config.yaml`: removed dead resume/phone/email/skills/experience/education/telegram fields; kept only `profile`, `scoring`, `search`, `llm`
- `requirements.txt`: trimmed to 8 core packages

**Cleanup fixes:**
- `.gitignore`: removed stale entries (`config/trusted_connections.yaml`, `*.json`, `Samples/`, erroneous `!.env.example` negation)
- `setup.sh`: fixed `requirements.full.txt` to `requirements.txt`, removed Telegram setup references, corrected README cross-reference from "Part 1" to "Part 2"
- `clean_db.sh`: rewrote to remove dead `applications` table reference; now only clears `seen_jobs`. Fixed DB path to `pathfinder/data/tracker.db` from repo root.
- `.sfdx/` directory accidentally staged with `git add -A`. Removed with `git rm -r --cached`, added to `.gitignore`.

**Key insight:** Dead code is a tax on every future reader. The Telegram bot and resume generator were real features that got built and abandoned. The right call was full deletion, not commenting out, so the repo tells a coherent story.

---

## Stage 11: Salesforce Career Pipeline Integration *(commits 56-62)*

**The problem:** Scored jobs existed only in an email digest. There was no way to track applications, move them through stages, or see the pipeline at a glance without leaving the inbox.

**What was built:** `push_to_salesforce()` in `pathfinder.py`. After scoring, YES and MAYBE jobs are pushed to Salesforce as Opportunities under a "Pathfinder" Account, linked to a "Job Candidate" Contact. The integration uses `simple-salesforce`, a Python wrapper over the Salesforce REST API. No managed package, no AppExchange install required.

**Key implementation details:**
- Auth via username/password/security token. Credentials in `.env`, loaded with `python-dotenv`.
- Dedup by `Job_Posting_URL__c`: queries Salesforce before each create; skips if the record already exists, preserving any stage the user has manually advanced.
- Stage mapping: YES to `"Job Identified"`, MAYBE to `"If you have time"`
- Source mapping: `linkedin` to `"LinkedIn"`, `indeed`/`glassdoor` to `"Job Board"`, else `"Other"`
- Work type: `"remote"` to Remote, `"hybrid"` to Hybrid, else On-Site
- SF credentials are optional. If `SF_USERNAME` is not set, the push step is silently skipped.

**Salesforce object setup:** Custom fields added to Opportunity: `Job_Posting_URL__c`, `Work_Type__c`, `Source__c`, `Hiring_Hypothesis__c`. Custom picklist stage "If you have time" added alongside "Job Identified".

**Errors fixed:**
- `load_dotenv('pathfinder/.env')` path not resolving from repo root on some runners. Wrapped in `os.path.abspath()`.
- `GMAIL_RECIPIENT` renamed to `DIGEST_RECIPIENT` everywhere (pathfinder.py, README, daily.yml, .env.example). "Gmail" implied a Gmail-only restriction that doesn't exist. Any SMTP recipient works.

**Key insight:** `simple-salesforce` is a thin HTTP wrapper. There's no package to install in Salesforce and no OAuth flow to configure for a personal org. Username + password + security token is enough for a single-user automation. The dedup-by-URL pattern means the tool can run daily without creating duplicates or overwriting records the user has already worked.

---

## Stage 12: Hiring Hypothesis *(commits 63-68)*

**The problem:** The one-sentence reason told you whether a job was relevant to you. It didn't tell you why the company was hiring, which is often the most useful signal when deciding whether to engage and how to frame an application.

**What was built:** A HYPOTHESIS block appended to the LLM scoring output for YES and MAYBE jobs:

```
HYPOTHESIS_CATEGORY: Backfill | Capacity | New capability | Recovery | Strategic bet | Unclear
HYPOTHESIS_SIGNAL: one to two sentences
```

Six forced-choice categories:
- **Backfill:** someone left, role is open
- **Capacity:** team growing under existing mandate
- **New capability:** hiring for something they don't currently have
- **Recovery:** previous implementation failed or stalled
- **Strategic bet:** org is making a deliberate platform shift
- **Unclear:** not enough signal in the posting

The hypothesis category and signal are stored on the job object and rendered as a teal-bordered block inside the email card and pushed to `Hiring_Hypothesis__c` in Salesforce.

**Implementation notes:**
- Prompt changed from "If SCORE is YES" to "If SCORE is YES or MAYBE". Hypothesis is useful regardless of fit level.
- `score_job()` parses four return values: `(score, reason, hyp_category, hyp_signal)`. Missing fields return `None` gracefully.
- `max_tokens` raised from 150 to 250 to accommodate the additional output.
- Token cost increase: roughly 60-80 tokens per YES/MAYBE job. At 3-5 relevant jobs per run, that's 300-400 additional tokens daily, negligible against the 100k free-tier limit.

**Wrong turn:** `--preview` sample jobs didn't include `hypothesis_category`/`hypothesis_signal` fields, so the hypothesis block never appeared during preview testing. All three sample jobs were updated with real hypothesis data.

**Key insight:** Forced-choice categorization is more useful than free-form inference. "Recovery" and "Strategic bet" produce very different outreach angles. A free-text field would collapse these distinctions. Making the LLM pick from six specific options produces consistent, actionable output.

---

## Stage 13: Email Accessibility and Font Polish *(commits 69-72)*

**The problem:** The email rendered well structurally but had readability issues: reason text was small, italic body text was invisible in dark mode, font sizes were inconsistent across card elements, and the hypothesis block had been accidentally dropped during a prior template rebuild.

**What was fixed:**

- **Dark mode body text:** Reason (rationale) text was italic and dark, unreadable on dark backgrounds. Changed to white (`#FFFFFF`) in dark mode via `.t-reason` class in `@media (prefers-color-scheme: dark)`, black in light mode. Applied `font-weight: 500` to ensure contrast without going full bold.
- **Font sizes normalized:** Title 17px, company 15px, location 13px, reason 15px, pipeline label 14px, section headers 12px, footer 13px, button 14px, hypothesis label 12px, hypothesis signal 15px. Reason text size raised to match card body (was 13px, now 15px).
- **Hypothesis block restored:** The teal-bordered block with category label and signal text had been dropped during a prior refactor of `build_html()`. Restored in `card_row()`, rendered for both YES and MAYBE jobs when `hypothesis_category` and `hypothesis_signal` are present.
- **Dark mode hypothesis background:** Added `.bg-hypothesis { background-color: #0D2B1A !important; }` to `@media` block so the hypothesis card uses a deep green-black in dark mode rather than inheriting a mid-gray.

**Key insight:** Email accessibility is mostly contrast and font size. The biggest wins are always: sufficient font size (14px minimum for body), explicit dark-mode overrides for text color, and not relying on color alone to convey meaning.

---

## Stage 14: Seen-Jobs Persistence on GitHub Actions *(commit 73)*

**The problem:** GitHub Actions runners are ephemeral. Every workflow run starts from a clean checkout. The `tracker.db` SQLite file that records seen job IDs was never persisted between runs, meaning every daily run re-scored every job and sent duplicates in every digest.

**What was built:** `actions/cache@v4` with a two-key pattern:

```yaml
- name: Restore seen-jobs cache
  uses: actions/cache@v4
  with:
    path: pathfinder/data/tracker.db
    key: tracker-db-${{ github.run_id }}
    restore-keys: |
      tracker-db-
```

**How it works:**
- On each run, the cache action looks for an exact match on `tracker-db-<run_id>`. Never found, since run IDs are unique.
- Falls back to `restore-keys: tracker-db-`, a prefix match that finds the most recent saved cache.
- Restores the DB from the previous run so seen job IDs carry forward.
- After the run completes, saves a new cache entry under the current run's unique key.
- Net result: the DB grows incrementally run-over-run, and jobs are never re-scored once seen.

**Key insight:** The `restore-keys` prefix pattern is the standard Actions idiom for rolling caches. The cache key must change each run (so the new state gets saved), but the restore fallback matches any prior entry. A static key would restore correctly but never save an update.

---

## Stage 15: Ghost Job Detection *(commit 3bcec80)*

**The problem:** Job boards are full of postings that aren't real open roles - companies that left a listing up after filling the position, recurring phantom postings used to build a candidate pipeline, or roles that were budget-approved but never actually got hiring manager sign-off. Scoring these jobs accurately doesn't matter if the job itself is dead. There was no signal for this at all.

**What was built:** `ghost_detector.py` - a post-scoring detection layer that runs on every QUALIFY and NEUTRAL result and returns one of four states:

| State | Meaning | Badge |
|---|---|---|
| Low Risk | Weak signal only - posting is 60+ days old | Amber |
| Unverified | Moderate signal - same role from this company found in prior search history | Orange |
| Ghost Likely | Strong signal - repost history combined with stale age | Red |
| clean | Inconclusive - not enough signal either way | No badge |

**Two detection signals:**

1. **Posting age** (weakest) - if `date_posted` is 60+ days ago, that's a weak negative. Stale alone → Low Risk.
2. **Repost history** (moderate) - queries `job_cache` in SQLite for the same company with a title that's ≥60% word-overlap and an earlier cached date. Found → Unverified. Combined with stale age → Ghost Likely.

**Careers page as candidate-facing output (not a signal):** `find_careers_page_url()` probes five standard URL patterns (`/careers`, `/jobs`, `/careers/open-roles`, `/about/careers`, `jobs.domain`) and returns the first that responds 200. The result is added to each email card as a "Check Careers Page →" link when found, or a red "No careers page found - possible ghost." line when not. The URL is cached for 7 days. Career page discovery was intentionally kept out of the detection scoring after DuckDuckGo and Google both blocked programmatic search - direct URL probing is reliable; search-backed presence checks are not.

**Badge rendering in email:** The title row of each card was restructured as a two-cell table (title left, badge right). This is required for Outlook - flexbox doesn't work in email clients. Badge uses inline background and text color with no external CSS dependency.

**Salesforce:** Ghost detection state written to `Ghost_Detection__c` (Text, 20 chars) on the Opportunity record. Field is omitted for `clean` results.

**Key insight:** The career page check is the most useful signal by far. Repost history catches recurring phantom listings. Age alone is a weak prior - a 65-day-old posting is suspicious, not disqualifying. The combination of all three, weighted asymmetrically, produces meaningful signal without too many false positives on legitimate slow-hiring companies.

---

## Stage 16: Cache Management and Retention Rules *(commit 3bcec80)*

**The problem:** `job_cache` and `career_page_cache` had no retention policy. Left unchecked, the repost-history check would eventually match against jobs so old they have no bearing on whether the current listing is a ghost. The career page cache had no expiry at all - a result from six months ago could suppress a live re-check indefinitely.

**What was built:** `run_cache_cleanup()` in `tracker.py`, called as the first step of every pipeline run (before scouting). Three rules enforced in sequence:

1. **90-day company inactivity expiry** - any company whose most-recent `job_cache` entry is older than 90 days gets all its entries deleted. This removes companies that have genuinely gone quiet rather than just trimming individual old records.
2. **10-entry-per-company cap** - for active companies, keeps only the 10 most-recent cache entries by `cached_at`. Implemented as a correlated subquery that counts how many rows for the same company have a newer or equal timestamp; rows ranked beyond 10 are deleted.
3. **Career page cache expiry** - entries in `career_page_cache` older than 90 days are deleted. The 7-day TTL in the ghost detector handles re-checks at query time; this sweep removes entries too old to ever be served as fresh.

**Logging:** `run_cache_cleanup()` returns a stats dict. The pipeline prints a single line when records were removed, suppressed entirely on clean runs.

**Key insight:** Rule 1 (company-level expiry, not row-level expiry) was the deliberate choice. Deleting individual old rows would leave partial company history that could still trigger repost detection against a job scraped a year ago. Deleting the whole company's history when they've been inactive treats it as a clean slate, which is the correct behavior for a signal about whether a company is actively cycling postings.

---

## Stage 17: Email Reply Feedback Loop *(commit 3bcec80)*

**The problem:** Ghost detection is a heuristic. DuckDuckGo results are noisy, repost matching is fuzzy, and the career page check fails silently for companies with unusual domain conventions. There was no way to correct a wrong detection without modifying code or the database directly.

**What was built:** A closed-loop feedback mechanism: reply to any Pathfinder digest email with a plain-text correction, and the next pipeline run applies it as an override.

**How it works:**

1. `reply_parser.py` connects to Gmail via IMAP (same credentials as the outbound digest - no additional setup required).
2. Searches for unread messages with `SUBJECT "Re: Pathfinder"`.
3. Loads all company names from `job_cache` (canonical spellings from actual scraped data).
4. For each reply, scans for company name mentions (substring match, case-insensitive).
5. For each mention, extracts the surrounding sentences and classifies the correction direction:
   - **`confirmed_real`** - "not a ghost", "confirmed real", "false positive", "still hiring", "actually live", "legit"
   - **`confirmed_ghost`** - "confirmed ghost", "false negative", "not hiring", "no longer open", "ghost job", "fake posting"
   - Bare "ghost" without a negation also maps to `confirmed_ghost`
6. Writes the override to the new `companies` table (`ghost_override`, `override_set_at`, `override_source = 'email_reply'`).
7. Marks the email as read so it's not processed again.

**Override is checked first in `detect_ghost()`:** if a `confirmed_ghost` override exists (within 90 days), the function returns `"Ghost Likely"` immediately and skips all signal checks. A `confirmed_real` override causes the function to fall through to normal detection - since "Verified" was removed as a state, confirmed_real simply clears the ghost flag rather than asserting one.

**Preview company exclusion:** The five dummy companies used in `--preview` mode (Acme Consulting, CloudCo, Ridge Partners, BuildCorp, NorthPeak Group) are listed in a `_PREVIEW_COMPANIES` frozenset in `reply_parser.py` and skipped before the matching loop runs. This prevents any coincidental real-world company with the same name from having its override silently blocked, and makes the intent of the exclusion explicit.

**Key insight:** The classification order matters. `_REAL_RE` is tested before `_GHOST_RE`, which is tested before bare-"ghost" matching. This ensures "not a ghost" and "false positive" always win over a sentence that happens to also contain the word "ghost" in describing the detection result rather than the job itself. The two-pass approach (explicit phrases first, bare keyword last) eliminates the most common classification error.

**Wrong turn:** The initial design loaded the override check as a top-level import in `ghost_detector.py`. Moved to a deferred `_tracker()` helper function to keep the module boundary clean and avoid import ordering constraints during testing.

---

## Stage 18: Codebase Refactoring Sprint *(commits)*

**The problem:** After 17 stages of iterative development, `pathfinder.py` had grown to ~920 lines and contained scoring logic, email HTML, Salesforce push, and the main run loop all in one file. Config keys were inconsistent between `config.yaml` and internal code. Logging was a mix of `print()` and `logger.*()`. Dead code from abandoned features was still present. Several modules duplicated constants.

**What was built - one task at a time:**

1. **Seen-jobs TTL** - added a 90-day expiry rule to `run_cache_cleanup()` in `tracker.py`. Previously, seen job IDs accumulated indefinitely with no cleanup.

2. **Career page cache schema fix** - `career_page_cache` table was storing a boolean (result found/not found). Redesigned to store the URL string directly (`url TEXT`), enabling the email card to render the actual link without a second lookup. Migration handles old schema on first run.

3. **Consolidated `_HEADERS`** - HTTP headers were duplicated in `ghost_detector.py`, `fetcher.py`, and `parser.py`. Extracted into `pathfinder/src/_http.py` and imported from there.

4. **Config naming split removed** - `profile_loader.py` was translating `config.yaml`'s `search:` key into `scout` and `discovery` keys internally. `scout.py` read from these translated keys. Removed the translator entirely; everything now reads `search` directly. No more internal/external naming mismatch.

5. **Logging strategy unified** - `pathfinder.py` used `print()` exclusively. Library modules used `logger.*()`. Added `logging.basicConfig` to `pathfinder.py`, added a module-level `logger`, and converted all operational status prints to `logger.info/warning/error`. `print_digest()` kept as `print()` since it's intentional formatted terminal output.

6. **Dead code deleted** - `fetcher.py` and `parser.py` were not imported anywhere. `format_scout_message()` in `scout.py` was not called anywhere. All deleted.

7. **`pathfinder.py` split** - 920-line monolith split into four files:
   - `pathfinder/src/scorer.py` - `load_config`, `build_scoring_prompt`, `score_job`, `score_all`
   - `pathfinder/src/digest.py` - `build_html`, `build_no_results_email`, `send_email`, `print_digest`
   - `pathfinder/src/salesforce.py` - `push_to_salesforce`
   - `pathfinder.py` - `main()` only (~150 lines)

**Key insight:** Each of these tasks was done and tested one at a time before moving to the next. The correct order mattered: config naming had to be fixed before the split, or the split would have locked in the wrong key names. Logging had to be unified before the split, or the new modules would have needed their own logging setup. Dead code had to be deleted before the split, or it would have been accidentally carried into the new files.

---

## Stage 19: Digest Quality and Public Repo Readiness *(commits 325edce → d4417f4)*

**The problem:** The public repo had several friction points for new users and the digest output had accumulated quality debt: the hypothesis was a single blended sentence that wasn't actionable, NO results were invisible (no way to audit scoring), the ghost tracker's "Low Risk" badge signalled caution when it should signal confidence, and the README and build guide hadn't kept pace with what the tool actually did.

**What was built - by commit:**

`325edce` **Ghost tracker documentation in README** - Added a sentence to the intro paragraph explaining the repost signal mechanism (company + title + date stored per run, earlier date = repost). Added a `### How the ghost tracker works` subsection in the Ghost detection episode covering the `seen_jobs` vs `job_cache` distinction, the 10-entry cap, and the 90-day company inactivity expiry. Clarified that a fresh install has no history and meaningful signal arrives after 3–4 weeks.

`ad42c5f` **Config flow fix for public forks** - Removed `config.yaml` from `.gitignore` so users with private forks can commit their config directly. Updated `daily.yml` to remove the `else cp config.example.yaml config.yaml` fallback - the workflow now writes the `CONFIG` secret if present, otherwise leaves the checked-out `config.yaml` untouched. Either path works; the workflow handles both without intervention. Updated README Episode 4 to reflect that committing `config.yaml` is correct for private forks.

`9a80dc1` / `fd783e2` **Git commands clarification in README** - Replaced the terse git command table with a plain-language explanation of what `git add .`, `git commit`, and `git push` each do and why all three are required. Added the three-command sequence to the "Adjusting over time" section. Fixed a missing dash in the `git push` line that slipped through.

`93a9912` **Scoring output refinement** - Expanded `REASON` from one sentence to two: sentence 1 names the key qualifier or disqualifier, sentence 2 explains specifically how it aligns with or conflicts with the candidate's background. Cut `HYPOTHESIS_SIGNAL` from two sentences to one to prevent the hypothesis from dominating the card. Raised `max_tokens` from 350 to 400.

`d8e48a9` **NO results added to email digest** - Every job scored NO now appears in a `SCORED NO (n)` section at the bottom of the digest. Each entry shows title · company - disqualify reason on one line with a "View posting" link below. Rationale: scoring transparency. If the disqualify criteria are miscalibrated, the NO section surfaces it immediately. Three NO sample jobs added to `SAMPLE_JOBS` so `--preview` renders the section.

`a0101e7` / `f16a38d` **Ghost signal appended to hypothesis** - After ghost detection runs, if the result is `Unverified` or `Ghost Likely`, a plain-text note is appended to the hypothesis signal: "Repost signal detected - verify this role is still open before applying" or "Strong repost history - this role may not be actively filling." Ghost note is stored as `job["ghost_note"]` and rendered separately from the LLM output. Sample jobs updated so `--preview` reflects the ghost note on flagged cards.

`d41efcd` **Low Risk badge recoloured green** - `Low Risk` (posting 60+ days old, no repost history) was rendering amber, implying caution. Reframed: no repost history on an old posting is a positive signal - the role has been consistently listed without recycling. Badge changed to green (`#166534` on `#DCFCE7`). README badge table updated to match.

`d4417f4` **Hypothesis split into two labelled signals** - The single `HYPOTHESIS_SIGNAL` field was replaced with two fields: `HYPOTHESIS_WHY` (why the company is hiring right now) and `HYPOTHESIS_VALUE` (what specific value the candidate brings to fill that gap). The prompt now instructs the LLM to produce both independently. The email card renders them as labelled lines: **Why they're hiring:** and **What you bring:**. The ghost note appends as a third muted italic line when present. All downstream consumers updated: `scorer.py` (prompt + parsing + return tuple), `digest.py` (card template + terminal output), `salesforce.py` (Hiring_Hypothesis__c field), `pathfinder.py` (sample data + ghost note logic). Preview SVG updated to reflect the new card layout across all three sections (QUALIFY, WORTH A LOOK, SCORED NO).

**Key insight:** The hypothesis split was the highest-leverage change in this stage. Knowing why a company is hiring tells you how to frame an application. Knowing what value you specifically bring tells you what to lead with. A single blended sentence collapsed those two signals into one that was harder to act on. Separating them makes the digest genuinely useful as interview prep, not just as a filter.

---

## What the finished system does

On a daily schedule at 6am PST:

1. **Reply parse** - connects to Gmail via IMAP, reads any unread Pathfinder reply emails, extracts ghost detection corrections, and writes overrides to the companies table
2. **Cache cleanup** - expires company records inactive for 90 days, trims each company to 10 repost-history entries, removes career page cache entries older than 90 days
3. **Scout** - searches LinkedIn for Salesforce consulting role titles across Canada; deduplicates across sources and skips jobs already seen in prior runs (persisted via Actions cache)
4. **AI filter** - drops obvious title mismatches in batches before scoring
5. **Score** - each job evaluated against the candidate profile: YES / MAYBE / NO with a two-sentence reason (key signal + why it aligns or conflicts with the candidate's background)
6. **Hypothesis** - YES and MAYBE jobs get a forced-choice hiring category (Backfill / Capacity / New capability / Recovery / Strategic bet / Unclear) with two labelled signals: why the company is hiring and what specific value the candidate brings
7. **Ghost detection** - each YES and MAYBE job checked against two signals (posting age, repost history); result rendered as a colour-coded badge (green = low risk, orange = unverified, red = ghost likely). Ghost note appended to hypothesis when flagged. A separate careers page probe adds a direct link or a red warning to each card.
8. **Email** - Outlook-compatible HTML digest sent with funnel metrics, scored cards with reason, hypothesis (why/value + ghost note), and ghost badge; NO results listed compactly at the bottom with disqualify reason and posting link for human review
9. **Salesforce** - YES and MAYBE jobs pushed as Opportunities, deduped by URL, with stage, source, work type, and ghost detection state mapped to custom fields

**Typical run:** ~300 scraped → ~120 AI filtered → 15-20 scored → 2-5 relevant. Token usage ~50-55k/day against a 100k daily limit on Groq's free tier.

**Stack:** Python 3.11 · python-jobspy · Groq (llama-3.3-70b-versatile) · SQLite · Gmail SMTP + IMAP · BeautifulSoup · simple-salesforce · GitHub Actions
