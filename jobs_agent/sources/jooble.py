"""Jooble adapter. https://jooble.org/api/about

Jooble aggregates other boards (Totaljobs, CV-Library, employers' own
sites), so it widens coverage beyond Reed and Adzuna; the same role arriving
from several boards is merged by the store's dedupe keys.

The API is one ``POST {host}/api/{key}`` with a JSON body. Two quirks shape
this module:

- The key is part of the URL, so an HTTP error's message would carry it into
  the logs. Every failure (not only HTTP ones: an invalid-URL or socket
  error quotes the URL too) is re-raised as :class:`JoobleError` without
  the URL or the original exception chained.
- ``salary`` is free text ("£28,000 - £32,000", "£12.50 an hour"), so it is
  left unset rather than guessed at; scoring treats it as unstated.
"""

from __future__ import annotations

import asyncio

import httpx

from ..models import Posting
from .base import clean, clean_line, is_anywhere, parse_date, request_with_retry

#: Jooble's own radius steps, in kilometres; anything else is rejected.
RADIUS_STEPS_KM = (0, 4, 8, 16, 26, 40, 80)
KM_PER_MILE = 1.609
#: Sent as the location for a whole-UK search: Jooble requires one.
WHOLE_UK = "United Kingdom"


class JoobleError(RuntimeError):
    """A Jooble request failed. Deliberately says nothing about the URL."""


def radius_km(miles: int) -> int:
    """The smallest Jooble step covering ``miles``, capped at its largest."""
    km = round(miles * KM_PER_MILE)
    return next((step for step in RADIUS_STEPS_KM if step >= km), RADIUS_STEPS_KM[-1])


def contract_type(raw: str | None) -> str | None:
    kind = (raw or "").lower()
    for word, value in (("contract", "contract"), ("temp", "temp"),
                        ("permanent", "permanent")):
        if word in kind:
            return value
    return None


class JoobleSource:
    name = "jooble"
    #: The UK host, so a bare place name like "Newport" resolves in the UK.
    BASE = "https://uk.jooble.org/api"
    PAGE = 50

    def __init__(self, api_key: str, max_concurrency: int = 2):
        self._url = f"{self.BASE}/{api_key}"
        self._sem = asyncio.Semaphore(max_concurrency)

    def _body(self, keyword: str, location: str, radius_miles: int, page: int) -> dict:
        place = ({"location": WHOLE_UK} if is_anywhere(location) else
                 {"location": location, "radius": str(radius_km(radius_miles))})
        return {"keywords": keyword, **place, "page": page, "ResultOnPage": self.PAGE}

    async def _post(self, client: httpx.AsyncClient, body: dict) -> dict:
        try:
            async with self._sem:
                r = await request_with_retry(client, "POST", self._url,
                                             json=body, timeout=30)
            return r.json()
        except httpx.HTTPStatusError as e:
            raise JoobleError(f"Jooble returned HTTP {e.response.status_code}") from None
        except Exception as e:  # noqa: BLE001 — any message may quote the keyed URL
            raise JoobleError(f"Jooble request failed: {type(e).__name__}") from None

    async def fetch(self, client: httpx.AsyncClient, keyword: str,
                    max_results: int = 300, *, location: str,
                    radius_miles: int) -> list[Posting]:
        out: list[Posting] = []
        page = 1
        while len(out) < max_results:
            payload = await self._post(client, self._body(keyword, location,
                                                          radius_miles, page))
            jobs = payload.get("jobs") or []
            if not jobs:
                break
            out.extend(self._to_posting(j) for j in jobs)
            if len(out) >= (payload.get("totalCount") or 0):
                break
            page += 1
            await asyncio.sleep(0.3)  # be polite
        return out[:max_results]

    @staticmethod
    def _to_posting(j: dict) -> Posting:
        return Posting(
            source="jooble",
            source_id=str(j.get("id")),
            title=clean_line(j.get("title")),
            employer=j.get("company") or "",
            location=j.get("location") or "",
            description=clean(j.get("snippet")),
            url=j.get("link") or "",
            posted=parse_date(j.get("updated"), "%Y-%m-%d"),
            contract_type=contract_type(j.get("type")),
        )
