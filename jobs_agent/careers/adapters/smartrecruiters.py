"""SmartRecruiters postings: https://developers.smartrecruiters.com/docs/posting-api

The list endpoint has no descriptions, and big employers have thousands of
postings, so we use its full-text search (``q``) for entry-level words and
the user's first few search terms, then keep relevant titles. Each
description needs one more request, so only the first
``MAX_DETAIL_FETCHES`` relevant jobs (likely-UK ones first) get it; the rest
are still returned, just without a description.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import MAX_DETAIL_FETCHES, iso_date, raw_job, relevant, search_queries, uk_first

ATS = "smartrecruiters"
API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
PUBLIC = "https://jobs.smartrecruiters.com/{company}/{id}"
PAGE = 100
# Order the job-ad sections appear in on the public page.
_SECTIONS = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")

log = logging.getLogger(__name__)


def _location(posting: dict) -> str:
    place = posting.get("location") or {}
    if place.get("fullLocation"):
        return place["fullLocation"]
    parts = (place.get("city"), place.get("region"), (place.get("country") or "").upper())
    return ", ".join(p for p in parts if p)


def _description(detail: dict) -> str:
    sections = ((detail.get("jobAd") or {}).get("sections")) or {}
    texts = []
    for key in _SECTIONS:
        section = sections.get(key) or {}
        body = html_to_text(section.get("text"))
        if body:
            texts.append(f"{section.get('title', '')}\n{body}".strip())
    return "\n\n".join(texts)


def _to_raw(company: str, posting: dict, detail: dict | None) -> dict:
    detail = detail or {}
    url = detail.get("postingUrl") or PUBLIC.format(company=quote(company, safe=""),
                                                    id=quote(str(posting.get("id")), safe=""))
    return raw_job(
        title=posting.get("name", ""),
        url=url,
        location=_location(posting),
        description=_description(detail),
        posted=iso_date(posting.get("releasedDate")),
        employment_type=(posting.get("typeOfEmployment") or {}).get("label"),
    )


async def _search(client: httpx.AsyncClient, company: str,
                  search_terms: list[str]) -> list[dict]:
    """Postings matching any search query, de-duplicated by id, in first-seen order."""
    seen: dict[str, dict] = {}
    for query in search_queries(search_terms):
        payload = await fetch_json(client, "GET", API.format(company=quote(company, safe="")),
                                   params={"limit": PAGE, "q": query})
        for posting in payload.get("content", []):
            seen.setdefault(str(posting.get("id")), posting)
    return list(seen.values())


async def _detail(client: httpx.AsyncClient, posting: dict) -> dict | None:
    """The posting's detail record, or None if it can't be read (logged)."""
    url = posting.get("ref") or ""
    if not url.startswith("https://api.smartrecruiters.com/"):
        return None  # never follow a ref pointing somewhere unexpected
    try:
        return await fetch_json(client, "GET", url)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("SmartRecruiters detail %s failed: %s", url, exc)
        return None


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant postings for company ``site.slug``; descriptions for the first few."""
    found = await _search(client, site.slug, search_terms)
    candidates = uk_first([p for p in found if relevant(p.get("name", ""), search_terms)],
                          _location)[:max_jobs]
    jobs = []
    for index, posting in enumerate(candidates):
        detail = await _detail(client, posting) if index < MAX_DETAIL_FETCHES else None
        jobs.append(_to_raw(site.slug, posting, detail))
    return jobs
