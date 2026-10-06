"""Job-board adapters and the fan-out that drives them.

``build_sources`` reads credentials from the environment and reports which
boards it had to skip, rather than exiting — the web server needs to turn
that into an HTTP error, not kill the process.
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

from ..models import Posting
from .adzuna import AdzunaSource
from .base import JobSource
from .reed import ReedSource

__all__ = [
    "AdzunaSource",
    "JobSource",
    "NoSourcesConfigured",
    "ReedSource",
    "build_sources",
    "gather_all",
]

log = logging.getLogger(__name__)


class NoSourcesConfigured(RuntimeError):
    """No job board had usable credentials in the environment."""


def build_sources(job_category: str = "") -> tuple[list[JobSource], list[str]]:
    """Build every adapter whose credentials are present.

    ``job_category`` is the profile's board-neutral sector slug; boards that
    can filter by sector (Adzuna) translate it, the rest ignore it.

    Returns ``(sources, warnings)``, where each warning names a board that
    was skipped for want of keys. Raises :class:`NoSourcesConfigured` if
    nothing is usable.
    """
    sources: list[JobSource] = []
    warnings: list[str] = []

    reed_key = os.getenv("REED_API_KEY")
    if reed_key:
        sources.append(ReedSource(reed_key))
    else:
        warnings.append("REED_API_KEY not set — skipping Reed")

    adzuna_id, adzuna_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if adzuna_id and adzuna_key:
        sources.append(AdzunaSource(adzuna_id, adzuna_key, category=job_category))
    else:
        warnings.append("ADZUNA_APP_ID/KEY not set — skipping Adzuna")

    if not sources:
        raise NoSourcesConfigured(
            "No job boards are configured. Set REED_API_KEY, or "
            "ADZUNA_APP_ID and ADZUNA_APP_KEY, and retry."
        )
    return sources, warnings


async def gather_all(sources: list[JobSource], keywords: list[str],
                     locations: list[str], *, radius_miles: int = 15,
                     per_keyword: int = 200) -> list[Posting]:
    """Fan out every keyword x location across every source concurrently.

    A board that errors is logged and skipped; the postings other boards
    returned are still worth having.
    """
    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [
            src.fetch(client, kw, per_keyword, location=loc, radius_miles=radius_miles)
            for src in sources
            for kw in keywords
            for loc in locations
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    postings: list[Posting] = []
    for res in results:
        if isinstance(res, BaseException):
            log.warning("source error: %s: %s", type(res).__name__, res)
            continue
        postings.extend(res)
    return postings
