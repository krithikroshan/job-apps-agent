"""Reading jobs out of a careers page's HTML.

Google for Jobs only lists vacancies that publish schema.org ``JobPosting``
data in a ``<script type="application/ld+json">`` block, so a great many
careers sites — including ones built on ATSs we have no adapter for — embed
exactly the structured fields we want: title, location, employer, salary,
date. ``job_postings`` reads those. When a page has none (often a listing
page whose individual job pages do), ``job_links`` finds the links that look
like job pages so the caller can visit them.

Everything uses the stdlib ``html.parser``: it is forgiving of broken
markup and adds no dependency.

Each job is a "RawJob" dict with exactly the keys in ``RAW_JOB_KEYS``.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit, urlunsplit

from .text import html_to_text

RAW_JOB_KEYS = ("title", "url", "location", "employer", "description", "posted",
                "salary_min", "salary_max", "employment_type")

MAX_LINKS = 100
_MAX_DEPTH = 8  # JSON-LD nesting we follow; real pages need 3 or 4
_JOB_PATH_HINTS = ("/job", "/jobs/", "/vacanc", "/position", "/opportunit",
                   "/role", "/requisition", "/posting")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_WS = re.compile(r"\s+")


# --- HTML scanning -----------------------------------------------------------

class _PageScanner(HTMLParser):
    """Collects JSON-LD script bodies and (text, href) pairs for every link."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._script: list[str] | None = None
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "script" and "ld+json" in (attributes.get("type") or "").lower():
            self._script = []
        elif tag == "a" and attributes.get("href"):
            self._link_href = attributes["href"]
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script is not None:
            self.scripts.append("".join(self._script))
            self._script = None
        elif tag == "a" and self._link_href is not None:
            text = _WS.sub(" ", "".join(self._link_text)).strip()
            self.links.append((text, self._link_href))
            self._link_href = None

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.append(data)
        elif self._link_href is not None:
            self._link_text.append(data)


def _scan(html: str) -> _PageScanner:
    scanner = _PageScanner()
    scanner.feed(html or "")
    scanner.close()
    return scanner


def page_links(html: str) -> list[tuple[str, str]]:
    """(link text, href as written) for every link on the page, in order."""
    return _scan(html).links


# --- JSON-LD traversal ---------------------------------------------------------

def _types(node: dict) -> set[str]:
    raw = node.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {v.rsplit("/", 1)[-1] for v in values if isinstance(v, str)}


def _walk(node: Any, depth: int = 0) -> Iterator[dict]:
    """Yield every JobPosting object in a JSON-LD document, in document order.

    Handles a bare object, a list of objects, ``@graph`` containers and
    ``ItemList``s whose elements are postings or ``ListItem``s wrapping them.
    """
    if depth > _MAX_DEPTH:
        return
    if isinstance(node, list):
        for item in node:
            yield from _walk(item, depth + 1)
        return
    if not isinstance(node, dict):
        return
    if "JobPosting" in _types(node):
        yield node
        return
    for key in ("@graph", "itemListElement", "item"):
        if key in node:
            yield from _walk(node[key], depth + 1)


def _load(block: str) -> Any:
    try:
        return json.loads(block)
    except ValueError:  # invalid JSON is common in the wild; skip the block
        return None


# --- field extraction ------------------------------------------------------------

def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name")
    return value.strip() if isinstance(value, str) else ""


def _address(address: Any) -> str:
    if isinstance(address, str):
        return address.strip()
    if not isinstance(address, dict):
        return ""
    parts = [_text(address.get(k))
             for k in ("addressLocality", "addressRegion", "addressCountry")]
    return ", ".join(p for p in parts if p)


def _location(posting: dict) -> str:
    places = posting.get("jobLocation")
    places = places if isinstance(places, list) else [places]
    found: list[str] = []
    for place in places:
        address = place.get("address") if isinstance(place, dict) else place
        text = _address(address)
        if text and text not in found:
            found.append(text)
    if not found and "TELECOMMUTE" in str(posting.get("jobLocationType", "")).upper():
        return "Remote"
    return "; ".join(found)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "")) if value is not None else None
    except ValueError:
        return None


# schema.org unitText values, as a multiplier to annual pay. HOUR, DAY and
# WEEK are left out on purpose: turning them into a yearly figure needs
# hours and weeks worked, which pages don't state, so a guess would mislead.
_ANNUAL_FACTOR = {"YEAR": 1, "ANNUAL": 1, "YEARLY": 1, "MONTH": 12, "MONTHLY": 12}
# With no unit stated, a figure below this can't be an annual salary.
_MIN_ANNUAL = 1000


def _salary(posting: dict) -> tuple[float | None, float | None]:
    """Annual GBP (min, max) from baseSalary, or (None, None).

    The app compares salaries in pounds per year, so another currency, or a
    unit we can't honestly annualise, is dropped rather than misreported.
    A missing currency is taken as GBP: the caller keeps UK jobs only.
    """
    salary = posting.get("baseSalary")
    if not isinstance(salary, dict):
        return None, None
    currency = salary.get("currency")
    if isinstance(currency, str) and currency.strip() and currency.strip().upper() != "GBP":
        return None, None
    value = salary.get("value")
    unit = salary.get("unitText")
    if isinstance(value, dict):
        unit = value.get("unitText") or unit
        exact = _number(value.get("value"))
        low = _number(value.get("minValue"))
        high = _number(value.get("maxValue"))
        low, high = (low if low is not None else exact), (high if high is not None else exact)
    else:
        low = high = _number(value)
    return _annual(low, unit), _annual(high, unit)


def _annual(amount: float | None, unit: Any) -> float | None:
    if amount is None:
        return None
    if not isinstance(unit, str) or not unit.strip():
        return amount if amount >= _MIN_ANNUAL else None
    factor = _ANNUAL_FACTOR.get(unit.strip().upper())
    return amount * factor if factor else None


def _employment_type(value: Any) -> str | None:
    if isinstance(value, list):
        value = ", ".join(v for v in value if isinstance(v, str))
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _posted(value: Any) -> str | None:
    if isinstance(value, str) and _ISO_DATE.match(value.strip()):
        return value.strip()[:10]
    return None


def _raw_job(posting: dict, base_url: str) -> dict | None:
    title = _text(posting.get("title")) or _text(posting.get("name"))
    if not title:
        return None
    url = posting.get("url")
    salary_min, salary_max = _salary(posting)
    description = posting.get("description")
    return {
        "title": html_to_text(title),
        "url": urljoin(base_url, url.strip()) if isinstance(url, str) and url.strip() else base_url,
        "location": _location(posting),
        "employer": _text(posting.get("hiringOrganization")) or None,
        "description": html_to_text(description if isinstance(description, str) else ""),
        "posted": _posted(posting.get("datePosted")),
        "salary_min": salary_min,
        "salary_max": salary_max,
        "employment_type": _employment_type(posting.get("employmentType")),
    }


def job_postings(html: str, base_url: str) -> list[dict]:
    """Every schema.org JobPosting on the page, as RawJob dicts."""
    jobs: list[dict] = []
    for block in _scan(html).scripts:
        for posting in _walk(_load(block)):
            job = _raw_job(posting, base_url)
            if job is not None:
                jobs.append(job)
    return jobs


# --- job links ---------------------------------------------------------------------

def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _looks_like_job(path: str) -> bool:
    lowered = path.lower()
    return any(hint in lowered for hint in _JOB_PATH_HINTS)


def job_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """(link text, absolute URL) for same-site links that look like job pages.

    Deduplicated by URL (ignoring #fragments and letter case: sites such as
    KPMG's link each vacancy twice, once with a lower-cased slug), in page
    order, at most MAX_LINKS.
    """
    site = _host(base_url)
    own = urlunsplit(urlsplit(base_url)._replace(fragment=""))
    seen: set[str] = {own.lower()}
    links: list[tuple[str, str]] = []
    for text, href in _scan(html).links:
        url = urlunsplit(urlsplit(urljoin(base_url, href.strip()))._replace(fragment=""))
        parts = urlsplit(url)
        if (not text or parts.scheme not in ("http", "https") or _host(url) != site
                or not _looks_like_job(parts.path) or url.lower() in seen):
            continue
        seen.add(url.lower())
        links.append((text, url))
        if len(links) >= MAX_LINKS:
            break
    return links
