Pathfinder: Cut the Job Search BS
A Consultant's Build Case Study
User Story
I am a Salesforce practitioner moving into consulting. I have operator credibility. I built and ran production orgs, ran $2.5M in revenue through a system I personally implemented, recovered a failed multi-partner implementation from scratch. What I do not have is consulting firm experience on a CV, which means I am competing for roles that require it while also being screened out by automated filters before a human reads my application.

The job search itself became the problem. Not finding roles. LinkedIn has plenty. Evaluating them. Thirty postings that all sound similar. No way to know whether a role is genuinely open, why the company is hiring, or whether my background is a real fit or a stretch. I was spending more time managing the search than preparing for interviews.

I built Pathfinder to solve that. It runs every morning while I sleep, scores every posting against my specific background, and sends me a short email with 2-5 roles worth pursuing and exactly why.

Situation: Ten job alerts. 40+ postings a day. 90% irrelevant, and the remaining 10% still required manual evaluation against a background that doesn't fit the standard consulting firm mould. The search was consuming the time needed to prepare for it.
Diagnosis: The problem wasn't finding postings. Every tool on the market scores keywords against a resume. None of them score a job description against a candidate's actual positioning: what they built, what they recovered, where they add value a generalist can't. Volume wasn't the constraint. Signal was.
Approach: Built a qualification pipeline on top of an open-source scraper. Candidate profile and QUALIFY/NEUTRAL/DISQUALIFY criteria defined in a single config file. LLM scores every posting against that profile using a fixed hierarchy: disqualifiers checked first, qualify signal strength determines YES or MAYBE, neutral signals reduce confidence without overriding a decision. A dual hypothesis fires on every qualified result: why the company is hiring, and what specific value the candidate brings to that problem. Ghost detection flags repeat postings and stale listings. Results push into a custom Salesforce Career Pipeline app as Opportunities, making the search a tracked pipeline with stage history.
Outcome: Built during the search it was designed to support. 300+ daily listings reduced to 2-5 worth pursuing. Hiring hypothesis replaces generic outreach with a specific angle. Ghost detection prevents effort on postings that aren't real opportunities. Results feed directly into a custom Salesforce Career Pipeline app, scored opportunities tracked through the full application process. $0 to run. 120+ commits. The company still uses the Salesforce org I implemented. This is the tool I built while looking for the next one.

The Problem
Job searching at volume has the same structural failure as a sales pipeline without qualification rules:

Criteria for what constitutes a good opportunity are implicit and inconsistent
Every evaluation is a fresh judgment call with no enforcement mechanism
Time is spent on discovery and filtering, not on engaging the right opportunities
There is no system of record, so patterns are invisible
Ghost postings, roles that are not actively being filled, consume real effort
Most tools address the discovery problem. More aggregation, more alerts, broader searches. That is the wrong lever. The discovery problem is already solved. LinkedIn returns hundreds of results. The qualification problem is what remains.

The specific costs:

Manual evaluation of a job posting takes 10-15 minutes to do properly
At 20-30 postings a day, that is 3-5 hours of evaluation with inconsistent output
Without a hypothesis on why a role is open, outreach and interview prep are generic
Without repost detection, time is spent applying to roles that have been posted for six months and are not being filled
What It Is
Pathfinder is a self-hosted job search intelligence system. It runs daily on GitHub Actions at no cost, searches LinkedIn for roles matching your target titles, scores every posting against your specific background using an LLM, and delivers a short HTML digest with only the roles worth engaging.

Each result includes:

A YES, MAYBE, or NO decision with a confidence level (HIGH, MEDIUM, LOW)
A plain-language reason grounded in verbatim evidence from the posting
A hiring hypothesis: why the company is hiring (Backfill, Capacity, New capability, Recovery, or Strategic bet) and what specific value you bring to that challenge
A ghost detection badge based on posting age and repost history
A direct link to the company careers page when one can be found
Results push optionally to Salesforce as Opportunities, making the search a tracked pipeline rather than a daily activity.

The tool is configured entirely through one YAML file. No code changes required to tune scoring criteria, target roles, or location preferences.

How It Works
Step 1: Discovery

A daily GitHub Actions workflow runs at 6am PST. It checks out the repo, restores the seen-jobs database from cache, applies secrets (API keys, config), and runs the main script.

The scout queries LinkedIn for each search term in the config (Salesforce Consultant, Salesforce Implementation Consultant, etc.) across the specified geography. Results are deduplicated against the seen-jobs database so previously scored postings are never re-evaluated. Title keyword filters and a lightweight AI pre-filter remove obvious mismatches before full scoring.

Typical funnel: 300 scraped, 120 pass the AI filter, 15-20 reach full scoring.

Step 2: Scoring

Each posting is sent to Groq's llama-3.3-70b with a structured prompt containing:

The candidate profile (name, framing, highlights, certifications, location)
The decision framework (tiered qualify signals, neutral signals, disqualify signals by category)
Company posting history from the local database (repost count, active role count, signals that inform the hypothesis)
The job description, capped at 3000 characters
The LLM applies signals in a fixed hierarchy:

Disqualifiers are checked first. Any single hard disqualifier scores the role NO immediately, regardless of how many qualify signals are present.
Qualify signal strength determines YES or MAYBE. Three or more core signals with no disqualifiers scores YES. Partial signals score MAYBE.
Neutral signals reduce confidence but cannot override a YES decision.
The LLM outputs a structured response: decision, confidence level, reasoning summary, top qualifier, triggered disqualifier (or none), and verbatim evidence from the posting. A hypothesis is generated for YES and MAYBE results only.

Step 3: Ghost Detection and Career Page Discovery

For each relevant result, the system checks the job cache for prior postings from the same company with a similar title. A prior posting with an earlier date is a repost signal. Combined with a posting age over 60 days, it flags as Ghost Likely. Repost signal without the age threshold flags as Unverified.

A parallel probe attempts to find the company's careers page by generating domain variants (hyphenated and plain slugs, common TLDs including .com, .ai, .io, .ca, .co) and firing HTTP requests simultaneously. The first successful response returns. The result is cached for seven days.

Step 4: Output

An HTML email digest is assembled and sent via Gmail SMTP. YES results appear first as full cards with reason, hypothesis, ghost badge, and careers page link. MAYBE results follow. NO results appear compactly at the bottom with the disqualifier reason for audit. If no relevant results are found, a no-results digest is sent so every run is accounted for.

Key Design Decisions
Explicit qualification rules, not flexible scoring

The first version used a flat list: qualify signals, neutral signals, disqualifiers. That works but treats all signals as equal weight. The production version uses tiered signals: core (drives YES decisions), strong (significant weight), supporting (tiebreakers only). This mirrors how a well-built lead scoring model in Salesforce works. A role with three supporting signals and nothing core does not score YES. A role with two core signals and one supporting signal does.

The disqualifiers are categorized: hard gates (location, authorization, salary floor), experience mismatch (consulting firm tenure, mandatory certifications), and domain lockout (deep industry expertise required). Categorization makes the reasoning traceable. The system tells you not just that a role is NO but why it is NO at the category level.

Confidence as a separate output from decision

YES/MAYBE/NO tells you what to do. HIGH/MEDIUM/LOW tells you how much to trust it. A MAYBE with HIGH confidence is different from a MAYBE with LOW confidence. The first is a real borderline fit. The second is a posting without enough information to decide. Collapsing those into a single score loses that distinction.

Hiring hypothesis split into two signals

The original version generated a single sentence explaining the hypothesis. That collapsed two different questions: why is this company hiring, and what do I specifically bring. They require different answers and serve different purposes. The WHY informs how to frame outreach. The VALUE informs what to lead with in an interview. A single sentence cannot do both without becoming vague.

The category (Backfill, Capacity, New capability, Recovery, Strategic bet) is a forced choice informed by both posting language and company tracking data. If the same company has posted a similar role twice in the past three months, that is a stronger signal of Backfill or Recovery than anything in the posting text. The hypothesis does not just read the posting. It reads the pattern.

Config-driven architecture

The qualification logic, candidate profile, search parameters, and scoring rules live entirely in one YAML file. The code reads the file; it does not contain the logic. This is the same principle that makes a well-built Salesforce org maintainable: business rules should be configurable by someone who understands the business, not hardcoded by someone who understands Python.

It also means iteration is fast. Scoring too aggressive? Edit one line. Want to add a new disqualifier? One line. No deployment, no code review, no risk of breaking something adjacent.

Ghost detection as a signal quality problem

Every CRM has stale data. Opportunities that stopped being real but were never closed. Contacts that left. Pathfinder has the same problem: postings that are technically live but not actively being filled. Ghost detection does not try to solve this definitively. It flags signals and lets the candidate decide. A green badge means low ghost risk. An orange badge means investigate before investing. A red badge means be skeptical. That is the right level of confidence for the available data.

Trade-offs
No UI. The digest email is the interface. Building a web UI would add infrastructure cost, maintenance overhead, and deployment complexity for a tool that needs to do one thing well. The value is in the decision engine, not the interface.

No multi-source scraping. Indeed and Glassdoor are in the config as commented-out options. They were tested and the scrapers are unreliable. LinkedIn is the only active source because reliable data is more valuable than broad data.

No automated response tracking. The Salesforce integration tracks what the system found and scored. Moving an Opportunity through the pipeline after that is a manual action. Automating follow-up tracking would require email parsing, CRM integration complexity, and assumptions about workflow that are too personal to be configurable. That is out of scope.

LLM as scorer, not ranker. The system makes a binary qualified/not-qualified decision with a confidence level. It does not produce a ranked list of the top ten results. Ranking implies a continuous preference ordering that the data does not support. A YES with HIGH confidence is a YES, and three of those in a week is three genuine opportunities. Ranking would encourage comparing opportunities that should each be evaluated on their own merit.

Outcome
The quantitative outcome: 300+ daily listings reduced to 2-5 qualified results with consistent criteria, zero manual filtering, and full reasoning attached to every decision.

The behavioral outcome is more significant. The search changed from browsing and reacting to evaluating and acting. Each result arrives with enough context to make an immediate decision: apply, investigate further, or skip. The hiring hypothesis replaces generic outreach with a specific angle. The ghost detection prevents effort on postings that are not real opportunities.

The Salesforce integration makes the search a pipeline with the same properties that make sales pipelines useful: a system of record, stage tracking, and a visible history of what was found and when.

What this demonstrates as a consultant:

The same principles applied here are the ones applied in a Salesforce implementation. Explicit qualification criteria enforced consistently. Business logic externalized and configurable. Outputs structured for downstream use without transformation. Historical data used to improve signal quality. A system built to change behavior, not just surface information.

The tool is running in production. It runs every morning. The config has been iterated against real job postings. The decision framework reflects what actually matters in the roles being targeted, not what seemed right in advance.

Stack: Python 3.11, python-jobspy, Groq (llama-3.3-70b-versatile), SQLite, Gmail SMTP and IMAP, BeautifulSoup, simple-salesforce, GitHub Actions. Total ongoing cost: $0.