"""Recognise which applicant-tracking system (ATS) hosts a careers URL.

Most employers don't build their own job boards; they rent one from an ATS
such as Greenhouse, Lever or Workday, and each of those serves every
customer's jobs from a predictable URL with a public JSON feed behind it.
Spotting the ATS from the URL lets an adapter use that structured feed
instead of scraping HTML. A link straight to a ``.json`` file on any other
host is read as a "JSON job index" (some firms publish their whole job list
that way, e.g. RSM's Adobe Edge Delivery index). Anything else is "generic"
and falls back to reading the page itself.

The slug ends up inside API URLs, so only plain identifiers are accepted —
letters, digits, ``_ . -``, no ``..`` — and anything else (a ``?for=``
query smuggling in a path, say) is treated as generic rather than trusted.

This is pure string work — no network — so it is cheap to call on every
URL a user types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

GENERIC = "generic"

_GREENHOUSE_HOSTS = {
    "boards.greenhouse.io", "job-boards.greenhouse.io",
    "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io",
}
_SIMPLE_HOSTS = {
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever-eu",
    "jobs.ashbyhq.com": "ashby",
    "careers.smartrecruiters.com": "smartrecruiters",
    "jobs.smartrecruiters.com": "smartrecruiters",
}
# <tenant>.wd<N>.myworkdayjobs.com — the wdN shard is part of the API URL.
_WORKDAY_HOST = re.compile(r"^([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com$")
# <employer>.pinpointhq.com — the subdomain is the employer; www is Pinpoint's own site.
_PINPOINT_HOST = re.compile(r"^([a-z0-9][a-z0-9-]{0,62})\.pinpointhq\.com$")
_NOT_EMPLOYERS = {"www", "app", "api"}
# Workday prefixes the site name with an optional locale: /en-GB/<site>/...
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$")
# An employer id as ATSs issue them; anything else could reshape the API URL.
_SAFE_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


@dataclass(frozen=True)
class CareersSite:
    """A careers URL and where its jobs can be read from.

    ``slug`` identifies the employer within its ATS (``""`` for generic);
    for Workday it is ``"tenant|wdN|site"`` because all three are needed,
    and for a JSON index it is the host (the URL itself is what's read).
    """
    ats: str
    slug: str
    url: str


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _greenhouse_slug(path: str, query: str) -> str:
    segments = _segments(path)
    # Embedded boards look like /embed/job_board?for=<slug>.
    if segments[:1] == ["embed"]:
        return parse_qs(query).get("for", [""])[0]
    return segments[0] if segments else ""


def _workday_slug(tenant: str, shard: str, path: str) -> str:
    segments = _segments(path)
    if segments and _LOCALE.match(segments[0]):
        segments = segments[1:]
    return f"{tenant}|{shard}|{segments[0]}" if segments else ""


def _safe(part: str) -> bool:
    return bool(_SAFE_SLUG.match(part)) and ".." not in part


def _valid(ats: str, slug: str) -> bool:
    """Whether ``slug`` is a plain identifier (for Workday: the site part;
    tenant and shard already passed the host pattern)."""
    if ats == "workday":
        return _safe(slug.rsplit("|", 1)[-1])
    return _safe(slug)


def _match(host: str, path: str, query: str) -> tuple[str, str]:
    """(ats, slug) for a known ATS host, or ("", "") if not recognised."""
    if host in _GREENHOUSE_HOSTS:
        return "greenhouse", _greenhouse_slug(path, query)
    if host in _SIMPLE_HOSTS:
        segments = _segments(path)
        return _SIMPLE_HOSTS[host], segments[0] if segments else ""
    workday = _WORKDAY_HOST.match(host)
    if workday:
        return "workday", _workday_slug(workday.group(1), workday.group(2), path)
    pinpoint = _PINPOINT_HOST.match(host)
    if pinpoint and pinpoint.group(1) not in _NOT_EMPLOYERS:
        return "pinpoint", pinpoint.group(1)
    if path.lower().endswith(".json"):
        # The adapter reads the URL itself; the host just names the source.
        return "json-index", host
    return "", ""


def detect(url: str) -> CareersSite:
    """Classify ``url``; unknown hosts, or ATS URLs with a missing or odd
    employer id, are generic."""
    url = url.strip()
    parts = urlsplit(url)
    ats, slug = _match((parts.hostname or "").lower(), parts.path, parts.query)
    if not ats or not slug or not _valid(ats, slug):
        return CareersSite(ats=GENERIC, slug="", url=url)
    return CareersSite(ats=ats, slug=slug, url=url)
