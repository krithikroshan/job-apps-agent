"""Shared plumbing for job-board adapters.

Every board here publishes a free, documented API for UK listings. We use
those rather than scraping: no browser automation, no ToS breach, no account
ban risk, and structured salary and contract-type fields instead of regex over
HTML.

Reed:      https://www.reed.co.uk/developers/jobseeker
Adzuna:    https://developer.adzuna.com/
Jooble:    https://jooble.org/api/about
Careerjet: https://www.careerjet.com/partners/api

Verify parameter names against those docs before first run — both APIs have
changed field names in the past. Each adapter is a separate module so a
breaking change on one board is a one-file fix.
"""

from __future__ import annotations

import asyncio
import html
import random
import re
from datetime import date, datetime
from typing import Optional, Protocol

import httpx

from ..models import Posting

_TAGS = re.compile(r"<[^>]+>")


class JobSource(Protocol):
    """What ``gather_all`` needs from an adapter."""

    async def fetch(self, client: httpx.AsyncClient, keyword: str,
                    max_results: int = 300, *, location: str,
                    radius_miles: int) -> list[Posting]:
        ...


def is_anywhere(location: str) -> bool:
    """True for a location meaning "the whole UK" (``profile.LOCATION_ANYWHERE``
    and its spellings), for which adapters omit their place parameter."""
    return location.strip().lower() in ("uk", "united kingdom", "anywhere")


def clean(text: str | None) -> str:
    """Strip HTML tags and unescape entities from a description body."""
    if not text:
        return ""
    return html.unescape(_TAGS.sub(" ", text)).strip()


#: A date far from any single-digit field, to measure how long ``fmt`` renders.
_SAMPLE = datetime(2000, 10, 10, 10, 10, 10)


def clean_line(text: str | None) -> str:
    """:func:`clean`, then whitespace collapsed — for one-line fields like
    titles, where aggregators wrap the matched keyword in ``<b>`` tags."""
    return " ".join(clean(text).split())


def parse_date(value: str | None, fmt: str) -> Optional[date]:
    """The date in the prefix of ``value`` that ``fmt`` describes; anything
    after it (seconds fractions, "Z", an offset) is ignored. Boards append
    those inconsistently, and only the day matters here."""
    if not value:
        return None
    try:
        return datetime.strptime(value[: len(_SAMPLE.strftime(fmt))], fmt).date()
    except (ValueError, TypeError):
        return None


async def get_with_retry(client: httpx.AsyncClient, url: str, *,
                         max_retries: int = 5, **kwargs) -> httpx.Response:
    """GET with exponential backoff on 429, honoring Retry-After when present."""
    return await request_with_retry(client, "GET", url, max_retries=max_retries, **kwargs)


async def request_with_retry(client: httpx.AsyncClient, method: str, url: str, *,
                             max_retries: int = 5, **kwargs) -> httpx.Response:
    """Any request, with :func:`get_with_retry`'s backoff on 429."""
    for attempt in range(max_retries + 1):
        r = await client.request(method, url, **kwargs)
        if r.status_code == 429 and attempt < max_retries:
            retry_after = r.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()  # pragma: no cover - loop always returns or raises above
    return r
