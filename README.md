# jobs-agent

Application pipeline for UK job hunting: fetch, score, deduplicate, draft,
and stage into a review queue. Ships with presets for graduate accounting
and for London legal and compliance roles; any field works once its scoring
profile is set.

**Nothing here submits an application.** A human approves and submits.

## Setup

```bash
pip install -r requirements.txt

cp .env.example .env    # then fill it in, or export the keys directly
```

| Variable | Needed for | Where |
|---|---|---|
| `DATABASE_URL` | everything | Supabase project -> Settings -> Database -> Connection string |
| `SUPABASE_URL` | signup/login | Supabase project -> Settings -> API -> Project URL |
| `SUPABASE_PUBLISHABLE_KEY` | signup/login | Supabase project -> Settings -> API Keys -> Publishable key (the legacy `SUPABASE_ANON_KEY` also works) |
| `REED_API_KEY` | fetching | https://www.reed.co.uk/developers/jobseeker |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | fetching | https://developer.adzuna.com/ |
| `JOOBLE_API_KEY` | fetching (optional) | https://jooble.org/api/about |
| `CAREERJET_API_KEY` (+ optional `CAREERJET_USER_IP`) | fetching (optional) | https://www.careerjet.co.uk/partners/api |
| `JOBS_AGENT_COMPANY_CHECKS_PER_DAY` | optional cap on company-site checks per user (default 60) | |
| `APP_ENCRYPTION_KEY` | users saving AI keys on /settings | `python -m jobs_agent gen-key` |
| `GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` | optional server-wide AI keys, used by anyone without their own | each provider's console (linked from /settings) |
| `JOBS_AGENT_GEMINI_MODEL` | optional default Gemini model | defaults to `gemini-3.6-flash` |

Both job-board API keys are free — Reed's is issued instantly, Adzuna's takes
a few minutes. Either board works on its own; a missing key skips that board
with a warning rather than failing.

```bash
python -m jobs_agent serve                          # web UI: sign up, then use it from the browser

# CLI commands operate on one account's data, so they need its Supabase Auth
# user id (Supabase dashboard -> Authentication -> Users -> copy the UID):
python -m jobs_agent fetch --user <user-id>
python -m jobs_agent queue --user <user-id> --min-score 40
python -m jobs_agent stats --user <user-id>
```

`.env` is read from the repository root, so the commands agree with each
other no matter which directory you run them from. The database is a
Postgres instance (Supabase), read from `DATABASE_URL`; override per-command
with `--db`.

## Web UI

`python -m jobs_agent serve` opens a local review UI. `/signup` creates an
account and `/login` signs into one (both via Supabase Auth); every other
page requires a signed-in session and shows only that account's own data —
see **Accounts and data segregation** below. Once signed in, there are two
pages:

- **Job Queue** (`/`) — fetch, filter by status/score/location (blank for
  anywhere; suggestions come from the profile's search locations), and move
  postings through the review workflow below.
- **Profile** (`/documents`) — your name, your CV, an example cover letter,
  and the scoring profile: a career preset to start from, where to search,
  and how postings are scored.
- **Companies** (`/companies`) — firms whose own careers sites to read: add
  a careers link, or one of the suggestions for your field.
- **Settings** (`/settings`) — AI providers (Gemini, OpenAI, Claude,
  OpenRouter): your own API key for each, the order they're tried in, and
  the model to use. "Test" checks a key by listing its models.

**Job sources.** "Fetch new listings" searches every job board with a key —
Reed, Adzuna (narrowed by the profile's job category), Jooble and Careerjet —
then reads each watched company's careers site that's due (every 12 hours,
one company per request so no request runs long). Company sites are read
through their applicant-tracking system's public job feed where there is one
(Workday, Greenhouse, Lever, Ashby, SmartRecruiters — detected from the
link), otherwise from the schema.org JobPosting data on the page or the job
links it lists. Only UK roles whose titles look relevant are kept, and then
scored, deduplicated and analysed like everything else. Every address is
checked before it's fetched (no private or internal hosts, on every
redirect), each site gets at most one request a second, and every fetch has
a time and size limit. Sites that only show jobs with JavaScript can't be
read; their Workday or Greenhouse link usually can.

**AI analysis.** After a fetch, the queue page has the AI read the top 150
postings by keyword score, six per request (`jobs_agent/analysis/`), against
the CV and target roles. For each it records the level and years asked for,
whether it's a graduate scheme, the qualifications required, study support,
what it says about visa sponsorship (with the sentence it came from — a quote
that isn't actually in the advert is thrown out), the closing date, a 0-100
fit with reasons and gaps, and red flags. Rows are ranked by **Match**: 40%
keyword score, 60% AI fit (keyword score alone until a posting is read).
Filters for visa, level, years, graduate schemes, study support and red
flags read these results. Analysis uses each provider's fast model, and only
re-runs when the CV, target titles or locations change.

**Suggestions.** The Profile page can suggest a scoring profile from the CV,
or from what's been shortlisted and rejected; the queue's search box turns a
plain-English request into its filters. Both are previews: nothing changes
until it's applied.

**AI providers.** Cover letters and the profile assistant go through
`jobs_agent/llm/`, which tries each provider the user has a key for, in
their order, falling through to the next on any failure (bad key, outage,
rate limit, a reply cut off mid-way). A user's own key wins over the
server's. User keys are Fernet-encrypted under `APP_ENCRYPTION_KEY` in the
`user_secrets` table and never returned by any endpoint — only their last
four characters.

**Presets and search area.** "Start from a preset" on the Scoring profile
tab loads a complete profile for a field (`jobs_agent/presets/`) into the
editor for review; nothing is saved until you save it. "Where to search"
takes up to five places (`UK` means anywhere) and a radius. Every target
title is searched in every place, strongest titles first, capped at 24
searches per board per fetch — a fetch that hits the cap says so.

Everything on the Profile page lives in the Postgres database: the CV as both
the original file (`files` table, re-downloadable from the page) and its
extracted text, the example letter and your name as text, and the scoring
profile as JSON (all in the `documents` table). Re-uploading or re-saving
overwrites in place; restarting the server keeps everything.

## Accounts and data segregation

Signup and login are handled by Supabase Auth (email + password) over its
REST API — see `web/auth.py`. A session is two httpOnly cookies (an access
token and a longer-lived refresh token); an expired access token is silently
refreshed from the refresh token on the next request. No password or session
token is ever stored in this app's own database.

Every table (`postings`, `applications`, `documents`, `files`) carries a
`user_id` column, and `storage/store.py`'s `Store` is constructed with one
signed-in user's id and scopes every query to it — see
`test_data_is_isolated_between_users` in `tests/test_store.py`. Postings are
fetched and stored independently per account rather than shared, so two
accounts can never see each other's queue, CV, letters, or scoring profile,
at the cost of each account triggering its own job-board API calls even for
an identical search.

**Review workflow**, backed by the `applications.status` column:

```
new / shortlisted --[Prepare application]--> drafted --[Approve]--> approved --[Mark as submitted]--> submitted
```

"Prepare application" sends the posting plus your CV and example letter to
your AI provider, which writes a complete cover letter tailored to that posting in your
voice. The result lands in `drafted`, editable in place, with a feedback box
that redrafts it. A posting can only be marked `submitted` once it is
`approved` — the server rejects the request otherwise. Nothing in this tool
ever calls a job board's apply endpoint; `submitted` just records that a
human did so elsewhere, so it drops out of the queue.

## Layout

```
jobs_agent/
  config.py       environment, paths, search keywords
  models.py       Posting and its deduplication keys
  profile.py      the scoring profile: dataclass, defaults, persistence
  presets/        one complete starting profile per career field
  scoring.py      deterministic relevance scoring
  pipeline.py     fetch -> score -> dedupe -> store, shared by CLI and web
  cli.py          argument parsing and console output only
  sources/        one module per job board, over a shared HTTP base
  careers/        company careers sites: safe fetching, ATS detection, adapters
  companies/      the watchlist: checking a company, suggested firms
  storage/        schema.sql and the Postgres Store (every table user_id-scoped)
  extract/        .docx / .pdf -> plain text
  letters/        drafting prompts, and the model call that runs them
  llm/            AI providers, per-user keys and settings, fallback client
  analysis/       AI reading of postings: prompt, output checks, batch runner
  suggest/        profile suggestions and plain-English search
  crypto.py       encryption of stored secrets
  web/            server, route table, API endpoints, Supabase Auth (auth.py),
                  and static/ assets
tests/            run with: python -m pytest
```

Two rules keep this navigable: `cli.py` and `web/` both depend on
`pipeline.py` and never on each other, and `web/api.py` endpoints are plain
functions of `(store, request)` so they can be tested without a socket.

## Design decisions worth arguing with

**APIs first, scraping only where it's the employer's own site.** Reed,
Adzuna, Jooble and Careerjet publish documented job APIs, and most employer
careers sites run on an applicant-tracking system with a public job feed.
Only when neither exists does the app read a careers page's HTML, and then
only the pages a user pointed it at. Scraping LinkedIn or Indeed would breach
their terms and break on every layout change; LinkedIn stays a manual
channel.

**Deterministic scoring first, an LLM second.** Every keyword score carries its
reasons, so when something irrelevant ranks high you can see which weight
caused it and fix it on the Profile page. Keyword scoring runs on everything
fetched, for free, and drops the obvious misses; only the top 150 survivors
are sent to a model, whose structured reading (not a bare number) is shown
next to the keyword breakdown, so the Match score stays explainable.

**Aggressive deduplication.** The same contract role is routinely posted by
four agencies under three titles. Identity is built from the normalised title,
location, and a fingerprint of the description body — agencies copy the body
verbatim, which is what makes this work. A looser title-plus-location key
catches reposts with lightly edited bodies.

**Exclusions are hard, not soft.** Seniority markers in the title and
experience requirements in the body drop a posting to score -1 and remove it.
Better to miss a stretch role than to bury the queue in things you can't get.

**Cover letters: full draft, human sign-off.** The model writes the whole
letter, tailored to the posting. It gets the CV as the only source of facts —
the prompt forbids inventing anything beyond it — and an example letter you
wrote yourself as the reference for voice, tone, and structure. Every draft is
read and edited in the queue before it can be `approved`; the letter is never
sent on the model's say-so.

**Approval is a hard gate, enforced server-side.** A posting can reach
`submitted` only by passing through `approved`; the API rejects the
transition otherwise, not just the UI.

## Known limitations

- Reed's and Adzuna's field names have changed before. Verify against their
  current docs on first run; each adapter is a single small module for that
  reason.
- Coverage excludes LinkedIn, and careers sites that only render jobs with
  JavaScript and have no job feed.
- The law preset's `title_blockers` include `counsel`, which will also drop
  legitimate "Legal Counsel Assistant" roles; the accounting preset's
  `qualified accountant` also drops "Part Qualified Accountant". Edit them on
  the Profile page if those segments matter.
- Freshness scoring assumes the posted date is real. Agencies repost stale
  roles with fresh dates; the dedupe catches most, not all.
- Editing the scoring profile affects the next fetch. Postings already in the
  queue keep the score they were stored with.
- Session cookies get the `Secure` flag only when `VERCEL` is set in the
  environment (see `web/auth.py`), so `serve`'s local `http://127.0.0.1`
  cookies stay usable; don't run this behind a real domain over plain HTTP.

## Next

Actual submission is still manual by design. A candidate next step is
per-site submission helpers (prefilling a Reed/Adzuna/firm application form),
each reviewed by a human before anything is sent.
