# Pathfinder: Automated Job Search Digest

Pathfinder runs every morning, searches LinkedIn for roles matching your target titles, scores each result against your background using AI, and emails you only the ones worth pursuing. Typically 2-5 results out of 300+ raw listings.

You set it up once. After that it runs on its own.

![Sample output showing a YES card with hiring hypothesis and a filtered NO card](docs/preview.svg)

---

## What this is

**Self-hosted.** Pathfinder runs entirely on your own GitHub account using free GitHub Actions. Nothing runs on any third-party server. No account to create, no subscription, no tracking.

**Non-monetized.** This is an open source tool. Free to use, free to modify, free to share.

**Low cost.** The AI scoring uses Groq's free tier. No credit card required. Gmail and GitHub are both free. Total ongoing cost: $0.

---

## Episode 1: What you need before you start

Watch this episode before installing anything. You'll create the accounts you need and gather your API keys so they're ready when you need them.

**Accounts to create:**

| Account | Where | Cost |
|---|---|---|
| GitHub | github.com | Free |
| Groq (AI scoring) | console.groq.com | Free |
| Gmail (needed to send digest) | gmail.com | Free |

**Time to complete setup:**
- Tools already installed (Python, Git, VS Code): 30-45 minutes
- Starting from scratch: 60-90 minutes
- Optional Salesforce integration: add 30 minutes

### Get your Groq API key

Groq runs the AI that scores job listings. Free, no credit card.

1. Go to [console.groq.com](https://console.groq.com)
2. Sign up with Google or email
3. Click **API Keys** in the left sidebar
4. Click **Create API Key**, name it "pathfinder"
5. Copy the key. It starts with `gsk_`.
6. Save it somewhere. You only see it once.

### Get your Gmail App Password

This lets Pathfinder send your digest without using your real Gmail password. Gmail requires 2-Step Verification to be on before this works.

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. Click **Security** in the left sidebar
3. Confirm **2-Step Verification** is turned on. If it's off, turn it on first.
4. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
5. Type "pathfinder" in the app name field, click **Create**
6. Copy the 16-character password (looks like: `abcd efgh ijkl mnop`)
7. Save it. Remove the spaces when you use it. It's one 16-character string.

---

## Episode 2: Install the tools

You need three things installed: VS Code (code editor), Python (runs Pathfinder), and Git (downloads the files). Install them in any order.

### VS Code

VS Code is the editor you'll use to view and edit your config files.

1. Go to [code.visualstudio.com](https://code.visualstudio.com)
2. Click **Download**. It detects your OS automatically.
3. Run the installer and accept all defaults.

### Python

Check if you already have it:
```
python --version
```

If you see `Python 3.10.x` or higher, skip this section.

**Windows:**
1. Go to [python.org/downloads](https://www.python.org/downloads/)
2. Click **Download Python 3.x.x**
3. Run the installer
4. **Check "Add Python to PATH"** on the first screen. This is easy to miss and required.
5. Click **Install Now**
6. Close and reopen your terminal, then run `python --version` to confirm

**Mac:**
```
brew install python3
```

If you don't have Homebrew:
```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then run `brew install python3`. Confirm with `python3 --version`.

### Git

Check if you already have it:
```
git --version
```

If not:
- **Windows:** Download from [git-scm.com](https://git-scm.com/download/win), run the installer, accept all defaults
- **Mac:** Run `brew install git`

**How to open a terminal:**
- **Windows:** Press `Win + R`, type `cmd`, press Enter. Or open VS Code and press `` Ctrl+` ``.
- **Mac:** Press `Cmd + Space`, type "Terminal", press Enter. Or open VS Code and press `` Cmd+` ``.

---

## Episode 3: Fork the repo and run setup

### Fork and clone

Fork first. This creates your own copy of Pathfinder on GitHub that you control.

1. Go to [github.com/wadecurtis/FS_Pathfinder](https://github.com/wadecurtis/FS_Pathfinder)
2. Click **Fork** (top right) then **Create fork**
3. Open VS Code, then open a terminal (`` Ctrl+` `` on Windows, `` Cmd+` `` on Mac)
4. Clone your fork:

```
git clone https://github.com/YOUR_USERNAME/Pathfinder.git
cd Pathfinder
```

Replace `YOUR_USERNAME` with your GitHub username.

5. Open the folder: **File > Open Folder** and select the `Pathfinder` folder.

### Run setup

The setup script creates your Python environment, installs dependencies, and copies the config template. Run it from the Pathfinder folder:

**Windows and Mac:**
```
bash pathfinder/setup.sh
```

> **Windows note:** Run this in Git Bash, not PowerShell or Command Prompt. Git Bash is installed with Git. Search "Git Bash" in the Start menu.

The script will:
- Create a `.venv` virtual environment
- Install all Python dependencies
- Copy `pathfinder/.env.example` to `pathfinder/.env`

You'll see green confirmation messages for each step.

### Activate the virtual environment

After setup, activate `.venv` before running any Python commands:

**Windows (Command Prompt):**
```
.venv\Scripts\activate
```

**Windows (PowerShell):**
```
.venv\Scripts\Activate.ps1
```

> If PowerShell says "running scripts is disabled": `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` then try again.

**Mac:**
```
source .venv/bin/activate
```

You'll see `(.venv)` at the start of your terminal line when it's active. You need this active every time you run Pathfinder locally.

---

## Episode 4: Fill in your config

Two files to fill in. Open them side by side in VS Code.

### pathfinder/.env

Open `pathfinder/.env`. Replace the placeholder values with the keys you got in Episode 1:

```
GROQ_API_KEY=gsk_your_key_here
GMAIL_SENDER=you@gmail.com
GMAIL_APP_PASSWORD=yoursixteencharpass
DIGEST_RECIPIENT=you@gmail.com
```

`DIGEST_RECIPIENT` is where the digest gets sent. It can be the same address as `GMAIL_SENDER`.

> `.env` is never uploaded to GitHub. It stays on your computer only.

### config.yaml

Open `config.yaml` at the root of the folder. It comes pre-filled with a real example. Replace each section with your own information.

---

#### profile: your background

The AI reads this every time it scores a job against you.

**`framing`** is one line that positions you. The AI leads with this when evaluating fit.
> Example: `"Salesforce practitioner moving into consulting"`

**`highlights`** are 6-8 facts the AI weighs every role against. These are the most important lines in the entire config. Write them as specific evidence, not vague claims.

| Weak | Strong |
|---|---|
| "Strong Salesforce experience" | "Built and ran two production Sales Cloud orgs end-to-end" |
| "Proven results" | "Ran 1,500+ deals and $2.5M revenue through a system I personally implemented" |
| "Fast learner" | "Completed UBC Sauder Business Analysis at 96% while earning 3 Salesforce certifications" |

The AI cannot score well on vague claims. Specific and outcome-focused highlights produce accurate results. Vague ones produce noise.

**`certifications_held`** and **`certifications_in_progress`**: list only what's true.

**`location_prefs`**: set your base city, which cities you'd accept hybrid in, and whether remote is OK.

**`languages`**: used to catch bilingual requirements you can't meet.

---

#### scoring: what makes a role a YES, MAYBE, or NO

The AI applies these rules exactly as written.

**`qualify`**: signals that push toward YES:
```yaml
qualify:
  - "Salesforce Sales Cloud as the primary platform"
  - "SMB or mid-market client base"
  - "Full lifecycle delivery: discovery through go-live"
```

**`neutral`**: present but not disqualifying:
```yaml
neutral:
  - "Enterprise clients (lower fit, not a dealbreaker)"
  - "Pre-sales or Solutions Engineer (skills transfer, different direction)"
```

**`disqualify`**: hard dealbreakers. Any single one scores the role NO immediately:
```yaml
disqualify:
  - "Requires Apex, LWC, or custom development"
  - "Requires bilingual French and English"
  - "Salary below $80K CAD"
  - "US-only role requiring US work authorization"
```

Be specific: `"Requires CPQ certification as mandatory"` not just `"CPQ"`.

---

#### search: what to search for

```yaml
search:
  queries:
    - "Salesforce Consultant"
    - "Salesforce Implementation Consultant"

  locations:
    - "Canada"    # Options: "Canada", "USA", "United Kingdom", etc.

  hours_old: 336      # How far back to look (336 = 2 weeks)
  max_per_query: 20   # Results per search query
```

Add as many `queries` as you have target titles. More queries means broader coverage.

---

#### llm

Leave this as-is.

---

## Episode 5: Test it locally

Before setting up automation, verify the pipeline works on your machine. Make sure `.venv` is active (you'll see `(.venv)` in your terminal).

### Check email rendering first

This sends a sample digest immediately. No job board queries, no scoring, no API tokens used:
```
python pathfinder.py --preview
```

Check your inbox. Confirm the email looks right before running anything else.

### Run a real test

This runs the actual pipeline in lightweight mode:
```
python pathfinder.py --test
```

Test mode:
- Searches your first 2 queries only
- Caps at 5 results per query
- Scores only the top 5 results
- Sends the real HTML email

Takes 1-2 minutes. The terminal shows each score and reason. If the right roles are scoring YES and your disqualifiers are catching what they should, you're ready.

### Troubleshooting

**`config.yaml not found`**
Run the command from the `Pathfinder` folder, not from inside `pathfinder/`.

**`GROQ_API_KEY not set`**
Your `.env` file is missing or in the wrong place. It should be at `pathfinder/.env`.

**`(.venv)` not showing**
Activate the virtual environment first (see Episode 3).

**`Invalid country string`**
`locations:` must be exactly `"Canada"` or `"USA"`, not `"United States"`.

**`No new listings found`**
LinkedIn rate-limits scrapers after repeated runs. Wait a few minutes and try again. If it persists, reduce `max_per_query` to 10.

---

## Episode 6: Go live

This makes Pathfinder run every morning automatically. Your computer does not need to be on.

### Add your secrets to GitHub

Go to your forked repo on GitHub, then Settings > Secrets and variables > Actions > New repository secret.

Add these four:

| Name | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq key (`gsk_...`) |
| `GMAIL_SENDER` | `you@gmail.com` |
| `GMAIL_APP_PASSWORD` | 16-character app password, no spaces |
| `DIGEST_RECIPIENT` | Where to send the digest (can be same address) |

### Trigger a test run

Go to your repo, click the **Actions** tab, select **Pathfinder - Daily Digest**, click **Run workflow**.

Wait 2-3 minutes. Check your inbox.

Green run plus email in your inbox means you're done. Pathfinder runs every morning automatically from here on.

### If the run fails

Click the failed run, then click **scout** in the left panel to expand the job log. Scroll to the red section to see the error.

Common causes:

| Error | Fix |
|---|---|
| `GROQ_API_KEY not set` | Secrets weren't added yet — go back to Settings > Secrets and add all four |
| `Authentication failed` | Gmail App Password is wrong or has spaces — re-enter it with no spaces |
| `No module named groq` | Dependencies failed to install — re-run the workflow |

If the error isn't in the table above, copy the red text and paste it into [Claude](https://claude.ai) with a description of what you were trying to do. It will tell you exactly what went wrong.

### Adjust the schedule

The default is 6am PST / 7am PDT daily. To change it, open `.github/workflows/daily.yml`:

```yaml
- cron: "7 14 * * *"   # UTC time
```

Format: `minute hour * * *`

| Time | Cron |
|---|---|
| 6am Vancouver PST (UTC-8) | `7 14 * * *` |
| 8am Vancouver PST (UTC-8) | `7 16 * * *` |
| 8am New York EST (UTC-5) | `7 13 * * *` |
| 8am London GMT (UTC+0) | `7 8 * * *` |
| 8am Sydney AEDT (UTC+11) | `7 21 * * *` |

Use an off-minute like `:07` instead of `:00`. Round numbers are busier on GitHub's scheduler and run later.

After changing it, save the file and push:
```
git add .github/workflows/daily.yml
git commit -m "update schedule"
git push
```

---

## Episode 7 (Optional): Salesforce Career Pipeline

If you use Salesforce, Pathfinder can push YES and MAYBE jobs directly into a Salesforce org as Opportunities. No manual data entry.

**What you'll need:**
- A Salesforce org (Developer Edition is free at [developer.salesforce.com](https://developer.salesforce.com))
- A Salesforce security token. Go to Settings > Reset My Security Token and Salesforce emails it to you.
- The `Job_Posting_URL__c` custom field on Opportunity (used for deduplication)

**Add to `pathfinder/.env`:**
```
SF_USERNAME=you@yourorg.com
SF_PASSWORD=yourpassword
SF_SECURITY_TOKEN=yourtoken
```

**How it works:**
- YES jobs land at stage `Job Identified`
- MAYBE jobs land at stage `If you have time`
- If the same job URL appears on a future run, it's skipped. It won't overwrite whatever stage you've moved it to.
- If credentials aren't set, Pathfinder skips the push silently.

**Also add to GitHub Actions secrets** if you want the daily automated run to push to Salesforce:
`SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`

---

## Adjusting over time

Everything is in `config.yaml`. After any change, push it to GitHub and the next run uses the updated config.

**Too many irrelevant results?** Add items to `disqualify`.

**Missing roles you expected?** Add more titles to `queries`.

**Scoring feels off?** Rewrite your `highlights`. More specific and outcome-focused always wins.

**Want a stricter YES?** Move items from `neutral` into `disqualify`.

To reset seen jobs so Pathfinder re-scores everything (useful after changing scoring criteria):
```
bash pathfinder/clean_db.sh
```

---

## Questions

If something breaks, bring the full error message and your `config.yaml` (with API keys removed) to [Claude](https://claude.ai) and describe what you expected versus what happened.
