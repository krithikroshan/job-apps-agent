"""Adapters that read jobs from one applicant-tracking system (ATS) each.

Every adapter exposes the same coroutine::

    fetch_jobs(client, site, *, search_terms, max_jobs=60) -> list[RawJob]

where a RawJob is a plain dict with exactly the keys in ``RAW_JOB_KEYS``.
``employer`` is always None: the caller knows the company the user added and
fills it in. Adapters don't filter by location either — the caller keeps UK
jobs — but they do drop titles that can't be relevant, because several ATS
APIs return a company's whole board (hundreds of senior roles) and some need
an extra, rate-limited request per job to read its description.

The shared helpers below are defined before the adapter imports on purpose:
each adapter imports them from this package, so they must exist by the time
the adapter modules load.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

MAX_DESCRIPTION_CHARS = 8000
# Per-job detail requests cost a polite one second each against one host.
MAX_DETAIL_FETCHES = 15
# Words that mark the entry-level roles this app searches for. Matched as
# whole words (plus a plural "s"), so the "-ship" forms are listed too.
GRADUATE_WORDS = ("graduate", "trainee", "traineeship", "apprentice", "apprenticeship",
                  "intern", "internship", "placement", "entry level", "early career",
                  "junior", "assistant")
# "Assistant" before these is a senior rank (AVP, Assistant Director), not
# an entry-level role; such phrases don't count as graduate words.
_SENIOR_ASSISTANT = re.compile(
    r"\bassistant[\s-]+(?:vice[\s-]+president|director|manager|head|general[\s-]+counsel)\b",
    re.IGNORECASE)
# Location fragments that suggest a UK job. Used only to decide which jobs
# get their (limited) detail fetches first, never to drop a job.
_UK_HINTS = ("united kingdom", "uk", "england", "scotland", "wales",
             "northern ireland", "london", "manchester", "birmingham", "leeds",
             "bristol", "edinburgh", "glasgow", "cardiff", "belfast")

RAW_JOB_KEYS = ("title", "url", "location", "employer", "description", "posted",
                "salary_min", "salary_max", "employment_type")


def _word_pattern(words: Iterable[str]) -> re.Pattern[str] | None:
    """One regex matching any of ``words`` as whole words, plural allowed.

    Whole words so "intern" doesn't match "International"; spaces in a
    phrase also match hyphens ("entry level" ~ "Entry-Level").
    """
    parts = [r"[\s-]+".join(re.escape(w) for w in word.split())
             for word in (w.strip().lower() for w in words if w) if word]
    if not parts:
        return None
    return re.compile(r"\b(?:%s)s?\b" % "|".join(parts), re.IGNORECASE)


_GRADUATE_PATTERN = _word_pattern(GRADUATE_WORDS)


def relevant(title: str, search_terms: list[str],
             graduate_words: Iterable[str] = GRADUATE_WORDS) -> bool:
    """True if ``title`` contains a search term or an entry-level word, as
    whole words in any case. "Assistant Director" and the like are senior,
    so they don't count as entry-level (a search term can still match)."""
    text = title or ""
    terms = _word_pattern(search_terms)
    if terms is not None and terms.search(text):
        return True
    words = (_GRADUATE_PATTERN if graduate_words is GRADUATE_WORDS
             else _word_pattern(graduate_words))
    return bool(words and words.search(_SENIOR_ASSISTANT.sub(" ", text)))


def looks_uk(location: str) -> bool:
    """Rough guess that ``location`` is in the UK (word match, so "uk" != "ukraine")."""
    text = (location or "").lower()
    words = set(text.replace(",", " ").replace("-", " ").replace("/", " ").split())
    return any((hint in text) if " " in hint or len(hint) > 3 else (hint in words)
               for hint in _UK_HINTS)


def uk_first(jobs: list[dict], location_of: Callable[[dict], str]) -> list[dict]:
    """``jobs`` reordered so likely-UK ones come first (stable otherwise)."""
    return sorted(jobs, key=lambda job: not looks_uk(location_of(job)))


def iso_date(value: Any) -> str | None:
    """"YYYY-MM-DD" from an ISO-8601 string or epoch milliseconds, else None."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def raw_job(*, title: str, url: str, location: str = "", description: str = "",
            posted: str | None = None, salary_min: float | None = None,
            salary_max: float | None = None,
            employment_type: str | None = None) -> dict:
    """A RawJob dict with every key present and the description capped."""
    return {
        "title": (title or "").strip(),
        "url": url or "",
        "location": (location or "").strip(),
        "employer": None,
        "description": (description or "")[:MAX_DESCRIPTION_CHARS],
        "posted": posted,
        "salary_min": float(salary_min) if salary_min is not None else None,
        "salary_max": float(salary_max) if salary_max is not None else None,
        "employment_type": employment_type or None,
    }


def search_queries(search_terms: list[str], limit: int = 3) -> list[str]:
    """Queries for search-style APIs: entry-level words, then the first few terms."""
    queries: list[str] = []
    for query in ("graduate", "trainee", *search_terms[:limit]):
        cleaned = (query or "").strip()
        if cleaned and cleaned.lower() not in (q.lower() for q in queries):
            queries.append(cleaned)
    return queries


# Adapter imports come after the helpers they use (see module docstring).
from . import (  # noqa: E402
    ashby, generic, greenhouse, json_index, lever, pinpoint, smartrecruiters, workday,
)

FetchJobs = Callable[..., Awaitable[list[dict]]]

ADAPTERS: dict[str, FetchJobs] = {
    greenhouse.ATS: greenhouse.fetch_jobs,
    **{ats: lever.fetch_jobs for ats in lever.ATS},
    ashby.ATS: ashby.fetch_jobs,
    smartrecruiters.ATS: smartrecruiters.fetch_jobs,
    workday.ATS: workday.fetch_jobs,
    pinpoint.ATS: pinpoint.fetch_jobs,
    json_index.ATS: json_index.fetch_jobs,
    generic.ATS: generic.fetch_jobs,
}


def get_adapter(ats: str) -> FetchJobs:
    """The adapter for ``ats``; unknown systems fall back to the generic reader."""
    try:
        return ADAPTERS[ats]
    except KeyError:
        return generic.fetch_jobs
