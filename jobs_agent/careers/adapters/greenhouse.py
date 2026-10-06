"""Greenhouse job boards: https://developers.greenhouse.io/job-board.html

One public GET returns the employer's whole board with descriptions
(``content=true``), so there are no per-job requests. ``content`` is HTML
escaped a second time inside the JSON (``&lt;p&gt;``); ``html_to_text``
handles that. Greenhouse publishes no salary or contract-type fields on this
endpoint, so those stay None.

Boards on the EU instance (job-boards.eu.greenhouse.io) are read from the
same API host: the EU API hostname is not publicly documented.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import iso_date, raw_job, relevant, uk_first

ATS = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def _to_raw(job: dict) -> dict:
    return raw_job(
        title=job.get("title", ""),
        url=job.get("absolute_url", ""),
        location=(job.get("location") or {}).get("name", ""),
        description=html_to_text(job.get("content")),
        posted=iso_date(job.get("first_published") or job.get("updated_at")),
    )


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant jobs from the board ``site.slug``, at most ``max_jobs``, likely-UK first.

    UK-first ordering happens before the cap so a global employer's long
    list of US roles can't crowd its London ones out.
    """
    payload = await fetch_json(client, "GET", API.format(token=quote(site.slug, safe="")),
                               params={"content": "true"})
    jobs = uk_first([j for j in payload.get("jobs", []) if relevant(j.get("title", ""), search_terms)],
                    lambda j: (j.get("location") or {}).get("name", ""))
    return [_to_raw(j) for j in jobs[:max_jobs]]
