"""Adzuna adapter. https://developer.adzuna.com/

Adzuna can narrow a search to a sector via ``category``, which takes its own
tags (listed by ``GET /v1/api/jobs/gb/categories``). The profile stores a
board-neutral slug; :data:`ADZUNA_CATEGORIES` translates it here, so another
board's sector scheme never leaks into the profile.
"""

from __future__ import annotations

import asyncio

import httpx

from ..models import Posting
from .base import clean, get_with_retry, is_anywhere, parse_date

#: ``profile.JOB_CATEGORIES`` slug -> Adzuna GB category tag.
ADZUNA_CATEGORIES: dict[str, str] = {
    "accounting": "accounting-finance-jobs",
    "legal": "legal-jobs",
    "it": "it-jobs",
    "engineering": "engineering-jobs",
    "marketing": "pr-advertising-marketing-jobs",
    "hr": "hr-jobs",
    "consultancy": "consultancy-jobs",
    "graduate": "graduate-jobs",
}


class AdzunaSource:
    name = "adzuna"
    BASE = "https://api.adzuna.com/v1/api/jobs/gb/search"
    PAGE = 50
    KM_PER_MILE = 1.609

    def __init__(self, app_id: str, app_key: str,
                 max_days_old: int = 21, max_concurrency: int = 2,
                 category: str = ""):
        self.app_id = app_id
        # An unmapped slug searches every sector rather than failing the board.
        self.category = ADZUNA_CATEGORIES.get(category, "")
        self.app_key = app_key
        self.max_days_old = max_days_old
        self._sem = asyncio.Semaphore(max_concurrency)

    async def fetch(self, client: httpx.AsyncClient, keyword: str,
                    max_results: int = 300, *, location: str,
                    radius_miles: int) -> list[Posting]:
        # Adzuna's distance is in kilometres.
        place = ({} if is_anywhere(location) else
                 {"where": location, "distance": round(radius_miles * self.KM_PER_MILE)})
        sector = {"category": self.category} if self.category else {}
        out: list[Posting] = []
        page = 1
        while len(out) < max_results:
            params = {
                "app_id": self.app_id,
                "app_key": self.app_key,
                "what": keyword,
                **place,
                **sector,
                "results_per_page": self.PAGE,
                "max_days_old": self.max_days_old,
                "content-type": "application/json",
            }
            async with self._sem:
                r = await get_with_retry(client, f"{self.BASE}/{page}",
                                         params=params, timeout=30)
            results = r.json().get("results", [])
            if not results:
                break
            out.extend(self._to_posting(j) for j in results)
            page += 1
            await asyncio.sleep(0.3)
        return out[:max_results]

    @staticmethod
    def _to_posting(j: dict) -> Posting:
        ct = (j.get("contract_type") or "").lower() or None
        return Posting(
            source="adzuna",
            source_id=str(j.get("id")),
            title=j.get("title", ""),
            employer=(j.get("company") or {}).get("display_name", ""),
            location=(j.get("location") or {}).get("display_name", ""),
            description=clean(j.get("description")),
            url=j.get("redirect_url", ""),
            posted=parse_date(j.get("created"), "%Y-%m-%dT%H:%M:%S"),
            salary_min=j.get("salary_min"),
            salary_max=j.get("salary_max"),
            contract_type=ct,
        )
