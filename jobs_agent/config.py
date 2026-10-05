"""Shared configuration: environment, paths, and search terms.

Everything here is imported by both the CLI and the web server, so neither
has to import the other. Nothing in this module imports from the rest of the
package — it sits at the bottom of the dependency graph.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repository root — the package's parent directory. `.env` is anchored here
#: rather than the current working directory so `fetch` and `serve` agree on
#: configuration no matter where they're launched from.
ROOT = Path(__file__).resolve().parent.parent

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
#: For bulk analysis of postings: answers a batch in seconds rather than a
#: minute, and was far less often overloaded in testing.
DEFAULT_GEMINI_FAST_MODEL = "gemini-flash-lite-latest"


def gemini_model() -> str:
    """Default Gemini model, when the user hasn't chosen one on the Settings
    page. Flash handles a one-page letter well and keeps the per-application
    cost negligible; override with JOBS_AGENT_GEMINI_MODEL.

    Read on each call rather than at import time, so it picks up whatever
    ``load_dotenv`` put in the environment regardless of import order.
    """
    return os.getenv("JOBS_AGENT_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


def gemini_fast_model() -> str:
    """Gemini model for analysing postings; override with
    JOBS_AGENT_GEMINI_FAST_MODEL."""
    return os.getenv("JOBS_AGENT_GEMINI_FAST_MODEL", DEFAULT_GEMINI_FAST_MODEL)


#: Search terms fanned out across every configured job board.
KEYWORDS = [
    "compliance analyst",
    "compliance officer",
    "financial crime analyst",
    "AML analyst",
    "KYC analyst",
    "regulatory compliance",
    "client onboarding analyst",
    "paralegal",
    "legal assistant",
    "legal analyst",
    "graduate compliance",
]


def database_url() -> str:
    """Postgres connection string. Required; set ``DATABASE_URL`` (locally in
    ``.env``, and as an environment variable in the Vercel project settings
    for deployments). Override per-invocation with ``--db``.

    Read lazily rather than at import time, so importing this module (or
    ``jobs_agent.web``) never requires it configured.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env locally (the Supabase "
            "connection string), and as an environment variable in the "
            "Vercel project settings for deployments."
        )
    return url


def supabase_url() -> str:
    """Supabase project URL, used for Auth (signup/login/session) over its
    REST API. Project Settings -> API -> Project URL.

    Read lazily, same reasoning as :func:`database_url`.
    """
    url = os.getenv("SUPABASE_URL")
    if not url:
        raise RuntimeError(
            "SUPABASE_URL is not set. Add it to .env locally (Project "
            "Settings -> API -> Project URL), and as an environment "
            "variable in the Vercel project settings for deployments."
        )
    return url.rstrip("/")


def supabase_api_key() -> str:
    """Supabase publishable key, sent as the ``apikey`` header on every Auth
    call. Project Settings -> API Keys -> Publishable key.

    Falls back to ``SUPABASE_ANON_KEY``, the legacy anon key, for projects
    still on the old key system — Auth accepts either in this header. Never
    sent as a bearer token: that header carries the user's session JWT.
    """
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_PUBLISHABLE_KEY is not set. Add it to .env locally "
            "(Project Settings -> API Keys -> Publishable key), and as an "
            "environment variable in the Vercel project settings for "
            "deployments."
        )
    return key


def load_dotenv(path: str | Path | None = None) -> None:
    """Read KEY=VALUE lines from a .env file into ``os.environ``.

    Minimal on purpose — no python-dotenv dependency. Handles comments, blank
    lines, an optional leading ``export``, and single/double-quoted values.
    Anything it can't parse is skipped silently. Real environment variables
    always win, so exporting a key overrides the file.
    """
    p = Path(path) if path is not None else ROOT / ".env"
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
