"""Lever postings: https://github.com/lever/postings-api

One public GET returns every published posting, descriptions included, so
there are no per-job requests. Lever runs separate US and EU instances; an
employer exists on only one, which ``detect`` tells apart from the careers
URL (jobs.lever.co vs jobs.eu.lever.co), so this module serves both ATS ids.
People don't always link the right one (Quantinuum's jobs are on the EU
instance only), so a 404 from one instance is retried on the other.

A posting's text is split across ``descriptionPlain`` (intro and body),
``lists`` (titled HTML bullet lists such as "Requirements") and
``additionalPlain``; we stitch them back together in page order.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import iso_date, raw_job, relevant, uk_first

ATS = ("lever", "lever-eu")
API = {
    "lever": "https://api.lever.co/v0/postings/{company}",
    "lever-eu": "https://api.eu.lever.co/v0/postings/{company}",
}
_OTHER = {"lever": "lever-eu", "lever-eu": "lever"}
_ANNUAL = "per-year-salary"


def _description(posting: dict) -> str:
    parts = [posting.get("descriptionPlain") or html_to_text(posting.get("description"))]
    for section in posting.get("lists") or []:
        parts.append(f"{section.get('text', '')}\n{html_to_text(section.get('content'))}")
    parts.append(posting.get("additionalPlain") or "")
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _salary(posting: dict) -> tuple[float | None, float | None]:
    """Annual GBP range if the posting publishes one; other currencies are ignored."""
    pay = posting.get("salaryRange") or {}
    if pay.get("currency") != "GBP" or pay.get("interval") != _ANNUAL:
        return None, None
    return pay.get("min"), pay.get("max")


def _location(posting: dict) -> str:
    categories = posting.get("categories") or {}
    return categories.get("location") or ", ".join(categories.get("allLocations") or [])


def _to_raw(posting: dict) -> dict:
    categories = posting.get("categories") or {}
    low, high = _salary(posting)
    return raw_job(
        title=posting.get("text", ""),
        url=posting.get("hostedUrl", ""),
        location=_location(posting),
        description=_description(posting),
        posted=iso_date(posting.get("createdAt")),
        salary_min=low,
        salary_max=high,
        employment_type=categories.get("commitment"),
    )


async def _postings(client: httpx.AsyncClient, ats: str, company: str) -> list[dict]:
    """The company's postings from instance ``ats``, or from the other one if
    this one has never heard of it (404). Other errors are the caller's."""
    params = {"mode": "json"}
    try:
        return await fetch_json(client, "GET", API[ats].format(company=company), params=params)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
    return await fetch_json(client, "GET", API[_OTHER[ats]].format(company=company),
                            params=params)


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant postings for company ``site.slug``, at most ``max_jobs``, likely-UK
    first (ordered before the cap, so UK roles aren't the ones cut)."""
    ats = site.ats if site.ats in API else "lever"
    payload = await _postings(client, ats, quote(site.slug, safe=""))
    postings = uk_first([p for p in payload if relevant(p.get("text", ""), search_terms)],
                        _location)
    return [_to_raw(p) for p in postings[:max_jobs]]
