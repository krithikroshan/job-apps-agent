"""Shared plumbing for job-board adapters.

Both Reed and Adzuna publish free, documented APIs for UK listings. We use
those rather than scraping: no browser automation, no ToS breach, no account
ban risk, and structured salary and contract-type fields instead of regex over
HTML.

Reed:   https://www.reed.co.uk/developers/jobseeker
Adzuna: https://developer.adzuna.com/

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


def parse_date(value: str | None, fmt: str) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value[: len(fmt.replace("%", "")) + 6], fmt).date()
    except (ValueError, TypeError):
        return None


async def get_with_retry(client: httpx.AsyncClient, url: str, *,
                         max_retries: int = 5, **kwargs) -> httpx.Response:
    """GET with exponential backoff on 429, honoring Retry-After when present."""
    for attempt in range(max_retries + 1):
        r = await client.get(url, **kwargs)
        if r.status_code == 429 and attempt < max_retries:
            retry_after = r.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else min(2 ** attempt, 30)
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()  # pragma: no cover - loop always returns or raises above
    return r
