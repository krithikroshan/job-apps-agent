"""A firm's whole job list published as one JSON file.

Some careers sites render their job search in the browser from a static
JSON index — RSM UK's ``/job-search-index.json`` (an Adobe Edge Delivery
"query index") lists every vacancy in one request. There's no standard
shape, so this adapter accepts ``{"data": [...]}`` or a bare ``[...]`` and
reads each field from the first of several common key names.

Values are tidied as such indexes tend to write them: a location like
``"offices:london,offices:milton-keynes"`` becomes "London, Milton Keynes";
dates may be epoch seconds, epoch milliseconds or ISO strings, and 0 means
"unknown"; links may be site-relative paths, resolved against the index's
own URL. A role-type field (``"jobs:role-type/graduate"``) counts towards
relevance, since scheme titles don't always contain an entry-level word.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from ..detect import CareersSite
from ..net import fetch_json
from ..text import html_to_text
from . import iso_date, raw_job, relevant, uk_first

ATS = "json-index"

# Candidate keys per field, in order of preference.
_TITLE = ("title", "jobTitle", "job_title", "name", "positionTitle")
_LOCATION = ("location", "jobLocation", "job_location", "locations", "city")
# The job's own page before an application form's link: it's what the user reviews.
_URL = ("url", "jobUrl", "link", "href", "path", "applyLink", "apply_url", "applyUrl")
_POSTED = ("created", "createdAt", "jobCreatedAt", "datePosted", "posted", "postedAt",
           "publishedAt", "published", "date")
_DESCRIPTION = ("description", "jobDescription", "summary", "teaser")
_KIND = ("roleType", "role_type", "type", "jobType", "category", "level")
_EMPLOYMENT = ("employmentType", "employment_type", "jobTime", "contractType")
# Epoch values above this are milliseconds (in seconds it would be year 5138).
_MS_THRESHOLD = 100_000_000_000


def _first(item: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", 0, [], {}):
            return value
    return None


def _label(value: str) -> str:
    """"offices:milton-keynes" -> "Milton Keynes"; plain text is left alone."""
    value = value.strip()
    if ":" not in value and "/" not in value:
        return value
    tail = value.replace(":", "/").rstrip("/").rsplit("/", 1)[-1]
    return " ".join(w.capitalize() for w in tail.replace("-", " ").replace("_", " ").split())


def _location(item: dict) -> str:
    value = _first(item, _LOCATION)
    if isinstance(value, list):
        parts = [v for v in value if isinstance(v, str)]
    elif isinstance(value, str):
        tagged = ":" in value or "/" in value
        parts = value.split(",") if tagged else [value]
    else:
        parts = []
    labels = [_label(p) for p in parts]
    return ", ".join(p for p in labels if p)


def _posted(item: dict) -> str | None:
    value = _first(item, _POSTED)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return iso_date(value if value > _MS_THRESHOLD else value * 1000)
    return iso_date(value)


def _text(item: dict, keys: tuple[str, ...]) -> str:
    value = _first(item, keys)
    return value if isinstance(value, str) else ""


def _kind(item: dict) -> str:
    return _label(_text(item, _KIND))


def _to_raw(item: dict, base_url: str) -> dict:
    link = _text(item, _URL).strip()
    return raw_job(
        title=html_to_text(_text(item, _TITLE)),
        url=urljoin(base_url, link) if link else "",
        location=_location(item),
        description=html_to_text(_text(item, _DESCRIPTION)),
        posted=_posted(item),
        employment_type=_text(item, _EMPLOYMENT) or None,
    )


def _rows(payload: Any) -> list[dict]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("the JSON file isn't a list of jobs")
    return [row for row in rows if isinstance(row, dict)]


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """Relevant jobs from the index at ``site.url``, at most ``max_jobs``,
    likely-UK first. Raises ValueError if the file isn't a job list."""
    rows = _rows(await fetch_json(client, "GET", site.url))
    jobs = [_to_raw(row, site.url) for row in rows
            if relevant(_text(row, _TITLE), search_terms) or relevant(_kind(row), [])]
    jobs = [job for job in jobs if job["title"]]
    return uk_first(jobs, lambda job: job["location"])[:max_jobs]
