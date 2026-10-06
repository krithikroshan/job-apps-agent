"""Workday careers sites (``<tenant>.<wdN>.myworkdayjobs.com/<site>``).

Workday has no documented public API, but every Workday careers page is
driven by the same JSON endpoints under ``/wday/cxs/<tenant>/<site>/``,
which is what we call. ``detect`` packs the three parts into the slug as
``"tenant|wdN|site"``.

Boards are large and global, so we use Workday's own search for
entry-level words and the user's first few search terms (20 results a
page; a second page only when the first was full), de-duplicate by
``externalPath`` and keep relevant titles. Descriptions need a request per
job, so only the first ``MAX_DETAIL_FETCHES`` (likely-UK first) get one; the
rest come back without a description. All told a check makes at most
``MAX_REQUESTS``: 5 queries x 2 pages + 15 details = 25.

A posting listed in several places shows only "3 Locations" until its
detail is read. Without the detail that says nothing about the country, so
the location is left empty — unknown — rather than passed on as if it were
a place.

The list gives only relative dates ("Posted 3 Days Ago"); the detail's
``startDate`` is the real one and wins when we have it.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from urllib.parse import quote

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import MAX_DETAIL_FETCHES, iso_date, raw_job, relevant, search_queries, uk_first

ATS = "workday"
BASE = "https://{tenant}.{shard}.myworkdayjobs.com"
PAGE = 20
SEARCH_PAGES = 2  # the second only when the first comes back full
# search_queries yields "graduate", "trainee" and up to 3 user terms.
MAX_REQUESTS = 5 * SEARCH_PAGES + MAX_DETAIL_FETCHES
_LOCATION_COUNT = re.compile(r"^\d+ Locations?$", re.IGNORECASE)
# Tenants come from the hostname; anything else would change the API host.
_TENANT = re.compile(r"^[a-z0-9-]+$")
_SHARD = re.compile(r"^wd\d+$")
_DAYS_AGO = re.compile(r"posted\s+(\d+)\s+days?\s+ago", re.IGNORECASE)

log = logging.getLogger(__name__)


def _parts(slug: str) -> tuple[str, str, str]:
    """(tenant, shard, site); ValueError if tenant or shard isn't a host label.

    ``detect`` already checks these, but slugs are stored, so check again
    before building a hostname out of one.
    """
    tenant, shard, site = slug.split("|", 2)
    if not _TENANT.match(tenant) or not _SHARD.match(shard):
        raise ValueError(f"not a Workday tenant/shard: {tenant!r}/{shard!r}")
    return tenant, shard, site


def relative_date(posted_on: str | None, today: date | None = None) -> str | None:
    """"YYYY-MM-DD" from Workday's "Posted Today/Yesterday/N Days Ago"; None for "30+"."""
    text = (posted_on or "").lower()
    today = today or date.today()
    if "30+" in text:
        return None
    if "today" in text:
        return today.isoformat()
    if "yesterday" in text:
        return (today - timedelta(days=1)).isoformat()
    match = _DAYS_AGO.search(text)
    return (today - timedelta(days=int(match.group(1)))).isoformat() if match else None


async def _search(client: httpx.AsyncClient, api: str, search_terms: list[str]) -> list[dict]:
    """Postings matching any search query, de-duplicated by externalPath."""
    seen: dict[str, dict] = {}
    for query in search_queries(search_terms):
        for page in range(SEARCH_PAGES):
            body = {"appliedFacets": {}, "limit": PAGE, "offset": page * PAGE,
                    "searchText": query}
            payload = await fetch_json(client, "POST", f"{api}/jobs", json=body)
            postings = payload.get("jobPostings") or []
            for posting in postings:
                path = posting.get("externalPath")
                if path:
                    seen.setdefault(path, posting)
            if len(postings) < PAGE:
                break  # a short page is the last one
    return list(seen.values())


async def _detail(client: httpx.AsyncClient, api: str, path: str) -> dict:
    """The posting's ``jobPostingInfo``, or {} if it can't be read (logged)."""
    try:
        payload = await fetch_json(client, "GET", f"{api}{path}")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Workday detail %s%s failed: %s", api, path, exc)
        return {}
    return payload.get("jobPostingInfo") or {}


def _location(info: dict, posting: dict) -> str:
    places = [info.get("location"), *(info.get("additionalLocations") or [])]
    joined = "; ".join(p for p in places if p)
    if joined:
        return joined
    listed = (posting.get("locationsText") or "").strip()
    return "" if _LOCATION_COUNT.match(listed) else listed  # "2 Locations": unknown


def _to_raw(public: str, posting: dict, info: dict) -> dict:
    return raw_job(
        title=posting.get("title", ""),
        url=info.get("externalUrl") or f"{public}{posting.get('externalPath', '')}",
        location=_location(info, posting),
        description=html_to_text(info.get("jobDescription")),
        posted=iso_date(info.get("startDate")) or relative_date(posting.get("postedOn")),
        employment_type=info.get("timeType") or posting.get("timeType"),
    )


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant postings on the Workday site in ``site.slug``; descriptions for the first few."""
    tenant, shard, board = _parts(site.slug)
    host = BASE.format(tenant=tenant, shard=shard)
    api = f"{host}/wday/cxs/{tenant}/{quote(board, safe='')}"
    found = await _search(client, api, search_terms)
    candidates = uk_first([p for p in found if relevant(p.get("title", ""), search_terms)],
                          lambda p: p.get("locationsText", ""))[:max_jobs]
    jobs = []
    for index, posting in enumerate(candidates):
        info = (await _detail(client, api, posting["externalPath"])
                if index < MAX_DETAIL_FETCHES else {})
        jobs.append(_to_raw(f"{host}/{quote(board, safe='')}", posting, info))
    return jobs
