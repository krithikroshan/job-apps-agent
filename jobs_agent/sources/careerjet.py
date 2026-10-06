"""Careerjet adapter (API v4). https://www.careerjet.com/partners/api

Careerjet aggregates UK boards and employer sites. v4 authenticates with
HTTP Basic auth (API key as username, empty password) and also *requires*
``user_ip`` and ``user_agent`` — the end user's, for its fraud checks. A
fetch here runs server-side for one account, so it sends the configured
``CAREERJET_USER_IP`` (the server's public address if unset) and this app's
own user agent.

Two response quirks:

- An ambiguous place name ("Newport") answers ``type: LOCATIONS`` with
  candidate places instead of jobs; that search is treated as empty.
- ``salary_min``/``salary_max`` are numbers, but per ``salary_type`` period
  (Y/M/W/D/H). Scoring compares annual GBP pay, so only yearly GBP figures
  are kept; the rest are left unstated rather than converted by guesswork.

Jobs carry no id, so the tracking ``url`` stands in as ``source_id``.
"""

from __future__ import annotations

import asyncio
from email.utils import parsedate_to_datetime

import httpx

from ..models import Posting
from .base import clean, clean_line, get_with_retry, is_anywhere

USER_AGENT = "jobs-agent/1.0 (+personal job search)"
#: Careerjet serves at most 10 pages of results per search.
MAX_PAGES = 10


def _posted(raw: str | None):
    try:
        return parsedate_to_datetime(raw).date() if raw else None
    except (TypeError, ValueError):
        return None


def _annual_gbp(j: dict) -> tuple[float | None, float | None]:
    if j.get("salary_type") != "Y" or j.get("salary_currency_code") != "GBP":
        return None, None
    return j.get("salary_min") or None, j.get("salary_max") or None


class CareerjetSource:
    name = "careerjet"
    BASE = "https://search.api.careerjet.net/v4/query"
    PAGE = 100  # Careerjet's maximum page_size

    def __init__(self, api_key: str, user_ip: str = "127.0.0.1",
                 max_concurrency: int = 2):
        self.auth = (api_key, "")
        self.user_ip = user_ip
        self._sem = asyncio.Semaphore(max_concurrency)

    def _params(self, keyword: str, location: str, radius_miles: int, page: int) -> dict:
        # en_GB radii are in miles; with no location the search is UK-wide.
        place = ({} if is_anywhere(location) else
                 {"location": location, "radius": radius_miles})
        return {
            "keywords": keyword,
            **place,
            "locale_code": "en_GB",
            "page": page,
            "page_size": self.PAGE,
            "user_ip": self.user_ip,
            "user_agent": USER_AGENT,
        }

    async def fetch(self, client: httpx.AsyncClient, keyword: str,
                    max_results: int = 300, *, location: str,
                    radius_miles: int) -> list[Posting]:
        out: list[Posting] = []
        page = 1
        while len(out) < max_results and page <= MAX_PAGES:
            async with self._sem:
                r = await get_with_retry(
                    client, self.BASE, auth=self.auth, timeout=30,
                    params=self._params(keyword, location, radius_miles, page))
            payload = r.json()
            jobs = (payload.get("jobs") or []) if payload.get("type") == "JOBS" else []
            if not jobs:
                break
            out.extend(self._to_posting(j) for j in jobs)
            if page >= (payload.get("pages") or 0):
                break
            page += 1
            await asyncio.sleep(0.3)  # be polite
        return out[:max_results]

    @staticmethod
    def _to_posting(j: dict) -> Posting:
        salary_min, salary_max = _annual_gbp(j)
        return Posting(
            source="careerjet",
            source_id=j.get("url") or "",
            title=clean_line(j.get("title")),
            employer=j.get("company") or "",
            location=j.get("locations") or "",
            description=clean(j.get("description")),
            url=j.get("url") or "",
            posted=_posted(j.get("date")),
            salary_min=salary_min,
            salary_max=salary_max,
        )
