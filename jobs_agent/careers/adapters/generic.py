"""Jobs from a careers site we have no dedicated ATS adapter for.

Two strategies, cheapest first:

1. The careers page itself carries schema.org JobPosting JSON-LD (common,
   because Google for Jobs requires it). That is structured data for every
   job in one request, so we use it and stop.
2. Otherwise the page is usually a list of links to individual job pages.
   We follow only the links that look relevant — their text, or failing
   that the words in their path, matching one of the user's search terms or
   a graduate-level word — and only a handful of them, since each costs a
   polite one-second-per-host request. (The path matters on real sites:
   Deloitte lists "Assurance" under ``/UKEarlyCareers/``; KPMG's links say
   "View role" and keep the title in the slug.) Each job page is read for
   its own JSON-LD, or failing that its visible text and heading.

A single broken job page is logged and skipped; only a failure to read the
careers page itself is an error the caller sees.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote, urlsplit

import httpx

from ..detect import CareersSite
from ..jsonld import job_links, job_postings
from ..net import fetch_text
from ..text import html_to_text
from . import relevant

ATS = "generic"

MAX_DETAIL_PAGES = 12
MAX_DESCRIPTION_CHARS = 6000
# Words that mark entry-level roles, which is what this app searches for.
GRADUATE_WORDS = ("graduate", "trainee", "apprentice", "intern", "placement",
                  "entry", "junior", "early careers", "assistant")

_H1 = re.compile(r"<h1\b[^>]*>(.*?)</h1\s*>", re.IGNORECASE | re.DOTALL)
# Link text that names an action, not the job: the page's heading is the title.
_CALL_TO_ACTION = re.compile(
    r"^(?:view|read|see|apply|more|find out|learn|show|details?|click)\b", re.IGNORECASE)
# Word boundaries inside a path segment: "UKEarlyCareers" -> "UK Early Careers".
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-zA-Z])(?=\d)")
_PATH_SEPARATORS = re.compile(r"[/_\-.+]+")
_BODY = re.compile(r"<body\b[^>]*>(.*)</body\s*>", re.IGNORECASE | re.DOTALL)
# Site-wide navigation and footers repeat on every page; they are not the job.
_CHROME = re.compile(r"<(nav|footer)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)

log = logging.getLogger(__name__)


def _path_words(url: str) -> str:
    """The words in a link's path: "/UKEarlyCareers/JobDetail/x" -> "UK Early Careers Job Detail x"."""
    path = unquote(urlsplit(url).path)
    return " ".join(_CAMEL.sub(" ", part) for part in _PATH_SEPARATORS.split(path) if part)


def _is_relevant(link_text: str, url: str, search_terms: list[str]) -> bool:
    # Whole-word match (shared with the ATS adapters), so "entry" doesn't
    # pick up "Industry" nor "intern" "International".
    return (relevant(link_text, search_terms, GRADUATE_WORDS)
            or relevant(_path_words(url), search_terms, GRADUATE_WORDS))


def _title(link_text: str, page: str) -> str:
    """The link text, unless it is a "View role" button: then the page's heading."""
    if not _CALL_TO_ACTION.match(link_text):
        return link_text
    heading = _H1.search(page)
    return " ".join(html_to_text(heading.group(1)).split()) if heading else link_text


def _page_text(page: str) -> str:
    body = _BODY.search(page)
    content = _CHROME.sub(" ", body.group(1) if body else page)
    return html_to_text(content)[:MAX_DESCRIPTION_CHARS]


def _job_from_page(link_text: str, url: str, page: str) -> dict:
    """The page's own JobPosting if it has one (with the page's text if the
    posting has no description), else its title and body text."""
    postings = job_postings(page, url)
    if postings:
        posting = postings[0]
        return posting if posting["description"] else {**posting, "description": _page_text(page)}
    return {
        "title": _title(link_text, page),
        "url": url,
        "location": "",
        "employer": None,
        "description": _page_text(page),
        "posted": None,
        "salary_min": None,
        "salary_max": None,
        "employment_type": None,
    }


async def _fetch_detail(client: httpx.AsyncClient, link_text: str, url: str) -> dict | None:
    try:
        page = await fetch_text(client, url)
        return _job_from_page(link_text, url, page)
    except Exception as exc:  # one bad job page must not sink the whole site
        log.warning("skipping job page %s: %s", url, exc)
        return None


async def fetch_jobs(client: httpx.AsyncClient, site: CareersSite, *,
                     search_terms: list[str], max_jobs: int = 60) -> list[dict]:
    """RawJobs from ``site.url``; raises only if that page itself can't be read."""
    page = await fetch_text(client, site.url)
    postings = job_postings(page, site.url)
    if postings:
        return postings[:max_jobs]

    candidates = [(text, url) for text, url in job_links(page, site.url)
                  if _is_relevant(text, url, search_terms)]
    jobs: list[dict] = []
    # Sequential on purpose: the pages share a host, so the per-host rate
    # limit would serialise concurrent fetches anyway.
    for text, url in candidates[:min(MAX_DETAIL_PAGES, max_jobs)]:
        job = await _fetch_detail(client, text, url)
        if job is not None:
            jobs.append(job)
    return jobs
