"""Pinpoint job boards (``<employer>.pinpointhq.com``).

Every Pinpoint board serves its published postings at ``/postings.json``:
one public GET, descriptions included, so there are no per-job requests.
The text comes in titled HTML sections (the intro, "Key Responsibilities",
"Skills, Knowledge & Expertise", benefits), which we join in page order.

Pinpoint publishes no posting date (only an optional closing date), so
``posted`` stays None. Pay is reported only when the employer chose to show
it and it is an annual GBP figure. The department's division ("Early
Careers" at Menzies) also counts towards relevance, since scheme titles
don't always contain an entry-level word.
"""

from __future__ import annotations

import re

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import raw_job, relevant, uk_first

ATS = "pinpoint"
API = "https://{slug}.pinpointhq.com/postings.json"
# The slug becomes a hostname label; anything else could aim the request elsewhere.
_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# (header key, body key) for each section, in the order the board shows them.
_SECTIONS = (
    (None, "description"),
    ("key_responsibilities_header", "key_responsibilities"),
    ("skills_knowledge_expertise_header", "skills_knowledge_expertise"),
    ("benefits_header", "benefits"),
)
_ANNUAL = ("year", "annual", "yearly")


def _description(posting: dict) -> str:
    parts = []
    for header_key, body_key in _SECTIONS:
        body = html_to_text(posting.get(body_key))
        if not body:
            continue
        header = (posting.get(header_key) or "").strip() if header_key else ""
        parts.append(f"{header}\n{body}" if header else body)
    return "\n\n".join(parts)


def _location(posting: dict) -> str:
    """"City, postcode" — the postcode lets the UK filter recognise small towns."""
    place = posting.get("location") or {}
    parts = [place.get("city") or place.get("name"), place.get("postal_code")]
    return ", ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


def _salary(posting: dict) -> tuple[float | None, float | None]:
    if not posting.get("compensation_visible"):
        return None, None
    if (posting.get("compensation_currency") or "").upper() != "GBP":
        return None, None
    if (posting.get("compensation_frequency") or "").lower() not in _ANNUAL:
        return None, None
    return posting.get("compensation_minimum"), posting.get("compensation_maximum")


def _division(posting: dict) -> str:
    return ((posting.get("job") or {}).get("division") or {}).get("name") or ""


def _is_relevant(posting: dict, search_terms: list[str]) -> bool:
    return (relevant(posting.get("title", ""), search_terms)
            or relevant(_division(posting), []))


def _to_raw(posting: dict) -> dict:
    low, high = _salary(posting)
    return raw_job(
        title=posting.get("title", ""),
        url=posting.get("url", ""),
        location=_location(posting),
        description=_description(posting),
        salary_min=low,
        salary_max=high,
        employment_type=posting.get("employment_type_text") or posting.get("employment_type"),
    )


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant postings on board ``site.slug``, at most ``max_jobs``, likely-UK first."""
    slug = site.slug.lower()
    if not _LABEL.match(slug):  # detect checks this too, but slugs are stored
        raise ValueError(f"not a Pinpoint board name: {site.slug!r}")
    payload = await fetch_json(client, "GET", API.format(slug=slug))
    postings = [p for p in (payload.get("data") or []) if isinstance(p, dict)
                and _is_relevant(p, search_terms)]
    return [_to_raw(p) for p in uk_first(postings, _location)[:max_jobs]]
