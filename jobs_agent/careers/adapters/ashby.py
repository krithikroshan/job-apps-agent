"""Ashby job boards: https://developers.ashbyhq.com/docs/public-job-posting-api

One public GET returns the whole board with descriptions and, with
``includeCompensation=true``, structured pay, so there are no per-job
requests. Postings with ``isListed: false`` are hidden on the real board and
skipped here too.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import iso_date, raw_job, relevant, uk_first

ATS = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board/{name}"
_EMPLOYMENT_TYPES = {
    "FullTime": "Full-time",
    "PartTime": "Part-time",
    "Intern": "Internship",
    "Contract": "Contract",
    "Temporary": "Temporary",
}


def _salary(job: dict) -> tuple[float | None, float | None]:
    """Annual GBP salary range if published; other currencies are ignored."""
    components = (job.get("compensation") or {}).get("summaryComponents") or []
    for part in components:
        if (part.get("compensationType") == "Salary" and part.get("currencyCode") == "GBP"
                and part.get("interval") == "1 YEAR"):
            return part.get("minValue"), part.get("maxValue")
    return None, None


def _location(job: dict) -> str:
    places = [job.get("location") or ""]
    places += [s.get("location", "") for s in job.get("secondaryLocations") or []]
    return "; ".join(p for p in places if p)


def _to_raw(job: dict) -> dict:
    low, high = _salary(job)
    return raw_job(
        title=job.get("title", ""),
        url=job.get("jobUrl", ""),
        location=_location(job),
        description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
        posted=iso_date(job.get("publishedAt")),
        salary_min=low,
        salary_max=high,
        employment_type=_EMPLOYMENT_TYPES.get(job.get("employmentType"), job.get("employmentType")),
    )


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant listed jobs on board ``site.slug``, at most ``max_jobs``, likely-UK
    first (ordered before the cap, so UK roles aren't the ones cut)."""
    payload = await fetch_json(client, "GET", API.format(name=quote(site.slug, safe="")),
                               params={"includeCompensation": "true"})
    jobs = uk_first([j for j in payload.get("jobs", [])
                     if j.get("isListed", True) and relevant(j.get("title", ""), search_terms)],
                    _location)
    return [_to_raw(j) for j in jobs[:max_jobs]]
