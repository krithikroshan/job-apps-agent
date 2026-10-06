"""Find a company's careers site from its name and homepage.

Used when a user accepts an AI-suggested employer: the model's idea of a
careers URL is often stale or invented, so the site is found the way a
person would find it. Read the homepage and follow its "Careers" link
(early-careers pages first), and take the applicant-tracking system's job
board linked from there if there is one, since that has a job feed behind
it. Failing that, try ``/careers`` and ``/jobs``; failing that, ask
Greenhouse whether it hosts a board under the company's name.

Every request goes through ``net`` (SSRF-guarded on every redirect, paced
per host, size- and time-capped), and one search reads at most
:data:`MAX_FETCHES` pages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from .detect import GENERIC, CareersSite, detect
from .jsonld import job_links, job_postings, page_links
from .net import UnsafeURL, check_url, fetch_text, make_client

log = logging.getLogger(__name__)

#: Pages one search may request, whatever it finds.
MAX_FETCHES = 10
#: Careers pages followed from the homepage, best first.
MAX_CANDIDATES = 2
#: "Apply now" / "Job search" links followed from one careers page: the job
#: board is usually one click past the marketing page.
MAX_HOPS = 2
#: The whole search runs inside one web request.
FIND_DEADLINE_SECONDS = 30.0
COMMON_PATHS = ("/careers", "/jobs")

#: Feeds this app can read; a JSON index link on a homepage is rarely jobs.
_NOT_A_BOARD = (GENERIC, "json-index")
_EARLY_WORDS = ("graduate", "early career", "early-career", "student", "trainee",
                "emerging talent", "entry level", "school leaver", "apprentice", "campus",
                "internship")
_CAREER_WORDS = ("career", "jobs", "vacanc", "join us", "join-us", "work with us",
                 "work for us", "opportunit", "recruit", "join our team")
#: Links on a careers page that lead to the actual list of jobs.
_SEARCH_WORDS = ("job search", "job-search", "search jobs", "search roles", "search vacancies",
                 "vacanc", "apply", "view jobs", "view roles", "open roles", "our roles",
                 "current opportunities", "find a job", "see roles", "/jobs")
#: Paths that are stories about the firm, whatever words they contain.
_NOT_CAREERS_PATHS = ("/news", "/insight", "/blog", "/press", "/media", "/article", "/event",
                      "/stories", "/case-stud")
#: Query parameters that only track where a click came from.
_TRACKING_KEYS = ("source", "gclid", "fbclid", "mc_cid", "mc_eid")
#: Hosts whose "Careers"/"Jobs" links lead away from the employer's own site.
_ELSEWHERE = ("linkedin.com", "facebook.com", "twitter.com", "x.com", "instagram.com",
              "youtube.com", "tiktok.com", "glassdoor.com", "glassdoor.co.uk", "indeed.com",
              "reed.co.uk", "totaljobs.com", "cv-library.co.uk", "adzuna.co.uk")
_LEGAL_SUFFIXES = ("uk", "llp", "ltd", "limited", "plc", "group", "inc", "co")
_GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}"
_GREENHOUSE_BOARD = "https://boards.greenhouse.io/{slug}"


@dataclass(frozen=True)
class Found:
    site: CareersSite
    #: "job board" (an ATS with a feed) or "careers page" (read as HTML).
    via: str


class _Budget:
    """Counts requests so no search can wander the web."""

    def __init__(self, limit: int):
        self.left = limit

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _bare(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))


def _core_words(name: str) -> list[str]:
    """The name's words without trailing legal suffixes ("Ltd", "UK", "LLP")."""
    words = _words(name)
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words = words[:-1]
    return words


def slug_guesses(name: str) -> list[str]:
    """Ids an ATS might file ``name`` under, likeliest first."""
    words, core = _words(name), _core_words(name)
    guesses = ["".join(words), "-".join(words), "".join(core), "-".join(core)]
    return [g for i, g in enumerate(guesses) if g and g not in guesses[:i]]


def _core(name: str) -> str:
    return "".join(_core_words(name))


def _same_name(a: str, b: str) -> bool:
    return bool(_core(a)) and _core(a) == _core(b)


def _homepage(website: str) -> str:
    host = _bare(re.sub(r"^https?://", "", (website or "").strip().lower()).split("/", 1)[0])
    return f"https://{host}/" if host else ""


def _absolute(base: str, href: str) -> str:
    """``href`` resolved against ``base``, without its fragment or tracking
    parameters; "" for anything that isn't a web link."""
    parts = urlsplit(urljoin(base, (href or "").strip()))
    if parts.scheme not in ("http", "https"):
        return ""
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS])
    return urlunsplit(parts._replace(query=query, fragment=""))


def _elsewhere(host: str) -> bool:
    host = _bare(host)
    return any(host == h or host.endswith("." + h) for h in _ELSEWHERE)


def _has(words: tuple[str, ...], text: str) -> bool:
    return any(w in text for w in words)


def _links(html: str, base: str) -> list[tuple[str, str, bool]]:
    """(lowercased link text, absolute URL, whether it's on ``base``'s own
    site) for every web link on the page except links back to the page
    itself, to social sites or job aggregators, and to news stories."""
    home = _bare(urlsplit(base).hostname or "")
    out = []
    for text, href in page_links(html):
        url = _absolute(base, href)
        if not url or url.rstrip("/") == base.rstrip("/"):
            continue
        parts = urlsplit(url)
        host = _bare(parts.hostname or "")
        if _elsewhere(host) or _has(_NOT_CAREERS_PATHS, parts.path.lower()):
            continue
        on_site = host == home or host.endswith("." + home)
        out.append((" ".join(text.lower().split()), url, on_site))
    return out


def _board_root(site: CareersSite) -> CareersSite:
    """The board itself, not the one job a link went to: adapters read the
    board from the slug, and one board shouldn't be watched twice."""
    if site.ats == "workday":
        tenant, shard, name = site.slug.split("|")
        url = f"https://{tenant}.{shard}.myworkdayjobs.com/{name}"
    elif site.ats == "pinpoint":
        url = f"https://{site.slug}.pinpointhq.com/"
    else:
        url = f"https://{urlsplit(site.url).hostname}/{site.slug}"
    return detect(url)


def _board_links(html: str, base: str) -> list[CareersSite]:
    """ATS job boards linked from a page, early-careers boards first."""
    boards: list[tuple[int, CareersSite]] = []
    for text, href in page_links(html):
        url = _absolute(base, href)
        site = detect(url) if url else None
        if site and site.ats not in _NOT_A_BOARD:
            early = _has(_EARLY_WORDS, f"{text} {url}".lower())
            boards.append((0 if early else 1, _board_root(site)))
    return [site for _, site in sorted(boards, key=lambda b: b[0])]


def _lists_jobs(html: str, url: str) -> bool:
    return bool(job_postings(html, url) or job_links(html, url))


def _ranked(scored: dict[str, int]) -> list[str]:
    return sorted(scored, key=lambda u: -scored[u])


def _career_links(html: str, base: str) -> list[str]:
    """Links that look like the site's careers pages, early careers first.
    A link's text counts wherever it points (a firm's careers site often
    lives on its own domain); its path only on the firm's own site."""
    scored: dict[str, int] = {}
    for text, url, on_site in _links(html, base):
        path = urlsplit(url).path.lower() if on_site else ""
        if not (_has(_CAREER_WORDS + _EARLY_WORDS, text) or _has(_CAREER_WORDS, path)):
            continue
        score = 2 if _has(_EARLY_WORDS, f"{text} {path}") else 1
        scored[url] = max(score, scored.get(url, 0))
    return _ranked(scored)


def _search_links(html: str, base: str) -> list[str]:
    """Links from a careers page to its list of jobs, early careers first."""
    scored: dict[str, int] = {}
    for text, url, _ in _links(html, base):
        hint = f"{text} {urlsplit(url).path.lower()}"
        if _has(_SEARCH_WORDS, hint):
            scored.setdefault(url, 2 if _has(_EARLY_WORDS, hint) else 1)
    return _ranked(scored)


async def _get(client: httpx.AsyncClient, url: str, budget: _Budget) -> str | None:
    if not budget.take():
        return None
    try:
        return await fetch_text(client, url)
    except (httpx.HTTPError, UnsafeURL, ValueError) as e:
        log.info("couldn't read %s while finding a careers site: %s", url, e)
        return None


async def _from_page(client: httpx.AsyncClient, url: str, budget: _Budget,
                     ) -> tuple[Found | None, str | None]:
    """(a job board linked from ``url``, or None; the page's html, or None)."""
    html = await _get(client, url, budget)
    if html is None:
        return None, None
    boards = _board_links(html, url)
    return (Found(boards[0], "job board") if boards else None), html


async def _from_careers_pages(client: httpx.AsyncClient, urls: list[str],
                              budget: _Budget) -> Found | None:
    """The best place to read jobs from, reached from ``urls``. Best is a job
    board linked from one of them or one click past it; then a page that
    lists jobs (one of ``urls`` before anything past it); then whichever of
    ``urls`` answered first."""
    first_page = listing = None
    for url in urls:
        found, html = await _from_page(client, url, budget)
        if found:
            return found
        if html is None:
            continue
        first_page = first_page or url
        if listing is None and _lists_jobs(html, url):
            listing = url
        for hop in _search_links(html, url)[:MAX_HOPS]:
            found, page = await _from_page(client, hop, budget)
            if found:
                return found
            if listing is None and page is not None and _lists_jobs(page, hop):
                listing = hop
        if listing:
            break
    chosen = listing or first_page
    return Found(detect(chosen), "careers page") if chosen else None


async def _from_website(client: httpx.AsyncClient, website: str,
                        budget: _Budget) -> Found | None:
    home = _homepage(website)
    if not home:
        return None
    try:
        check_url(home)
    except UnsafeURL:
        return None
    found, html = await _from_page(client, home, budget)
    if found:
        return found
    candidates = _career_links(html, home)[:MAX_CANDIDATES] if html else []
    found = await _from_careers_pages(client, candidates, budget)
    if found:
        return found
    # No careers link that answers: try where careers pages usually live.
    for url in (home.rstrip("/") + p for p in COMMON_PATHS):
        if url not in candidates:
            found = await _from_careers_pages(client, [url], budget)
            if found:
                return found
    return None


async def _from_greenhouse(client: httpx.AsyncClient, name: str,
                           budget: _Budget) -> Found | None:
    """A Greenhouse board filed under the company's name. Its own name must
    match too: "acme" may well be someone else's board."""
    for slug in slug_guesses(name)[:3]:
        text = await _get(client, _GREENHOUSE_API.format(slug=slug), budget)
        try:
            board = json.loads(text) if text else None
        except ValueError:
            board = None
        if isinstance(board, dict) and _same_name(str(board.get("name") or ""), name):
            return Found(detect(_GREENHOUSE_BOARD.format(slug=slug)), "job board")
    return None


async def find_careers_site(client: httpx.AsyncClient, name: str,
                            website: str = "") -> Found | None:
    """Where ``name``'s jobs can be read, or None if nothing turned up."""
    budget = _Budget(MAX_FETCHES)
    return (await _from_website(client, website, budget)
            or await _from_greenhouse(client, name, budget))


def find_careers_site_sync(name: str, website: str = "") -> Found | None:
    """:func:`find_careers_site` on its own client, within
    :data:`FIND_DEADLINE_SECONDS`; running out of time finds nothing."""
    async def run() -> Found | None:
        async with make_client() as client:
            return await find_careers_site(client, name, website)
    try:
        return asyncio.run(asyncio.wait_for(run(), FIND_DEADLINE_SECONDS))
    except asyncio.TimeoutError:
        log.info("finding %s's careers site ran out of time", name)
        return None
