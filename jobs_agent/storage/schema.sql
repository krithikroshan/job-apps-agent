-- Every table is scoped by user_id (a Supabase Auth user id) so that each
-- signed-in account only ever sees its own postings, applications, and
-- documents. Not a foreign key into auth.users: that would make the
-- per-test isolated schema (see conftest.py) require real Supabase Auth
-- users to exist, and the app already only ever passes a user_id that came
-- from a verified Supabase session, so the FK would buy little.

CREATE TABLE IF NOT EXISTS postings (
    user_id       UUID NOT NULL,
    key           TEXT NOT NULL,
    soft_key      TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    title         TEXT NOT NULL,
    employer      TEXT,
    location      TEXT,
    description   TEXT,
    url           TEXT,
    posted        TEXT,
    salary_min    REAL,
    salary_max    REAL,
    contract_type TEXT,
    via_agency    INTEGER,
    score         INTEGER DEFAULT 0,
    score_reasons TEXT,
    first_seen    TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_postings_soft ON postings(user_id, soft_key);
CREATE INDEX IF NOT EXISTS idx_postings_score ON postings(user_id, score DESC);

CREATE TABLE IF NOT EXISTS applications (
    user_id     UUID NOT NULL,
    posting_key TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',
    letter      TEXT,
    notes       TEXT,
    updated     TEXT NOT NULL,
    PRIMARY KEY (user_id, posting_key),
    FOREIGN KEY (user_id, posting_key) REFERENCES postings(user_id, key)
);

-- Candidate materials and settings, one row per (user, document id): 'cv'
-- (extracted text), 'cv_filename', 'cover_letter_template', 'candidate_name',
-- 'scoring_profile' (the JSON-encoded Profile), and 'llm_settings' (AI
-- provider order and model choices, JSON). Small and few per user,
-- so no history — the UI overwrites in place.
CREATE TABLE IF NOT EXISTS documents (
    user_id UUID NOT NULL,
    id      TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    updated TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);

-- The original uploaded CV file, kept verbatim so the document itself
-- persists (re-downloadable, re-extractable), not just the text pulled from
-- it. One row per user, id 'cv'.
CREATE TABLE IF NOT EXISTS files (
    user_id  UUID NOT NULL,
    id       TEXT NOT NULL,
    filename TEXT NOT NULL,
    data     BYTEA NOT NULL,
    updated  TEXT NOT NULL,
    PRIMARY KEY (user_id, id)
);

-- A user's own API keys for AI providers, one row per (user, provider).
-- Only ever ciphertext (see crypto.py). ``last4`` is the one plaintext
-- fragment kept, so the Settings page can say which key is on file.
CREATE TABLE IF NOT EXISTS user_secrets (
    user_id    UUID NOT NULL,
    provider   TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    last4      TEXT NOT NULL,
    updated    TEXT NOT NULL,
    PRIMARY KEY (user_id, provider)
);

-- What the AI made of a posting (see analysis/), one row per (user,
-- posting). ``context`` is a hash of everything the analysis depended on
-- besides the posting itself (the CV, the profile, the prompt version):
-- when it no longer matches, the row is stale and the posting is analysed
-- again. A failed retry keeps the last good ``result`` to show meanwhile.
CREATE TABLE IF NOT EXISTS posting_analysis (
    user_id     UUID NOT NULL,
    posting_key TEXT NOT NULL,
    context     TEXT NOT NULL,
    status      TEXT NOT NULL,
    result      JSONB,
    model       TEXT,
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 1,
    updated     TEXT NOT NULL,
    PRIMARY KEY (user_id, posting_key),
    FOREIGN KEY (user_id, posting_key) REFERENCES postings(user_id, key) ON DELETE CASCADE
)
;

-- AI calls made on the server's own keys, per user per UTC day, so one
-- account can't run up the operator's bill. Calls on a user's own key
-- aren't counted.
CREATE TABLE IF NOT EXISTS ai_usage (
    user_id UUID NOT NULL,
    day     TEXT NOT NULL,
    calls   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
)
