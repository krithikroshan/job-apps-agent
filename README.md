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
| `GEMINI_API_KEY` | drafting cover letters | https://aistudio.google.com/apikey |
| `JOBS_AGENT_GEMINI_MODEL` | optional model override | defaults to `gemini-3.6-flash` |

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
Gemini, which writes a complete cover letter tailored to that posting in your
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
  storage/        schema.sql and the Postgres Store (every table user_id-scoped)
  extract/        .docx / .pdf -> plain text
  letters/        drafting prompts, and the model call that runs them
  web/            server, route table, API endpoints, Supabase Auth (auth.py),
                  and static/ assets
tests/            run with: python -m pytest
```

Two rules keep this navigable: `cli.py` and `web/` both depend on
`pipeline.py` and never on each other, and `web/api.py` endpoints are plain
functions of `(store, request)` so they can be tested without a socket.

## Design decisions worth arguing with

**APIs, not scraping.** Reed and Adzuna publish documented UK job APIs.
Scraping LinkedIn or Indeed would breach their terms, risk the account,
break on every layout change, and force regex parsing of salary out of HTML.
Between them these two APIs cover most agency-posted London contract listings.
LinkedIn stays a manual channel.

**Deterministic scoring, not an LLM.** Every score carries its reasons, so when
something irrelevant ranks high you can see which weight caused it and fix it
on the Profile page. That is not true of a model call, and at ingestion volume
the model calls would cost more than they're worth. Save the model for the
letters.

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
- Coverage excludes roles posted only on firm career pages or LinkedIn.
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
