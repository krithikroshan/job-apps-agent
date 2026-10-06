"""Checking one watched company: read its careers site, keep the UK roles,
score them against the profile, and stage them in the queue.

One company per call, because each call is one web request and a careers
site can take many seconds to read politely (one request per second per
host, see ``careers/net.py``). The queue page calls the check endpoint in a
loop instead.

A check never raises for anything the site did: an unsafe address, a
timeout, an adapter that couldn't parse the page —
each becomes a short message for the user, recorded on the company so the
Companies page can show it in red ink. The check is recorded however it
ends (even a database error or an interrupted request), so a failing site
moves to the back of the line instead of being retried on every run.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable
from urllib.parse import urlsplit

import httpx
import psycopg

from ..models import Posting
from ..profile import LOCATION_ANYWHERE, Profile, load_profile
from ..scoring import score_all
from ..storage import Store

log = logging.getLogger(__name__)

#: Target titles sent to the site's search, strongest first. Most careers
#: sites search one term per request, so each extra title is more load on
#: someone else's server; scoring still sees every title afterwards.
MAX_SEARCH_TERMS = 6

#: The most one site read may take, all requests together. Each request has
#: its own deadline, but a site with many pages (Workday: up to 25 requests,
#: a second apart) could still hold the web request open for minutes. Long
#: enough for one worst-case fetch (~35s: its deadline plus one retry) and a
#: few quick ones; a timeout just records the error, partial results go.
CHECK_DEADLINE_SECONDS = 60

#: A company checked more recently than this isn't due again. A careers
#: site changes over days, not minutes, and every check is load on it.
RECHECK_AFTER = timedelta(hours=12)
_RETRY = (f"It'll be tried again in {int(RECHECK_AFTER.total_seconds() // 3600)} hours, "
          "or press Check now.")

#: Ways of naming the country itself. A role "in the UK" is kept whatever
#: follows it ("Remote - UK, US" is still open to the UK).
UK_COUNTRY_MARKERS = (
    "united kingdom", "uk", "gb", "gbr", "great britain", "britain", "england",
    "scotland", "wales", "northern ireland",
)
#: UK towns and cities, where graduate employers actually hire. Matched as
#: whole words, so "uk" doesn't match "Ukraine". Many share a name with a
#: place abroad (London, Ontario; Reading, PA), so a match followed by a
#: foreign region doesn't count — see ``_FOREIGN_REGIONS``. Ambiguous names
#: that mostly mean somewhere else (Perth) are left out: "Perth, Scotland"
#: is kept by "scotland". Jersey, Guernsey and the Isle of Man are left out
#: on purpose: they aren't the UK for visas or tax.
UK_PLACES = (
    "london", "city of london", "canary wharf", "croydon", "manchester", "salford",
    "birmingham", "solihull", "wolverhampton", "coventry", "leamington spa", "warwick",
    "leeds", "bradford", "wakefield", "huddersfield", "harrogate", "york", "hull",
    "sheffield", "doncaster", "rotherham", "newcastle", "gateshead", "sunderland",
    "durham", "middlesbrough", "darlington", "liverpool", "warrington", "chester",
    "stockport", "bolton", "wigan", "preston", "blackpool", "lancaster", "carlisle",
    "nottingham", "leicester", "loughborough", "derby", "lincoln", "stoke",
    "northampton", "milton keynes", "peterborough", "cambridge", "norwich", "ipswich",
    "colchester", "chelmsford", "luton", "watford", "st albans", "stevenage",
    "hemel hempstead", "oxford", "reading", "slough", "bracknell", "high wycombe",
    "guildford", "woking", "crawley", "brighton", "maidstone", "canterbury",
    "southampton", "portsmouth", "winchester", "basingstoke", "farnborough",
    "bournemouth", "salisbury", "swindon", "bristol", "bath", "gloucester",
    "cheltenham", "worcester", "telford", "exeter", "plymouth", "taunton", "truro",
    "cardiff", "swansea", "newport", "wrexham", "edinburgh", "glasgow", "aberdeen",
    "dundee", "stirling", "inverness", "livingston", "paisley", "belfast", "derry",
    "londonderry", "lisburn",
)
#: Regions and countries that, after a comma, put a UK-named place abroad.
#: Full names match the start of the part after the comma.
_FOREIGN_NAMES = (
    "canada", "ontario", "quebec", "british columbia", "alberta", "manitoba",
    "saskatchewan", "nova scotia", "new brunswick", "newfoundland",
    "usa", "u s a", "united states", "america", "pennsylvania", "massachusetts",
    "new hampshire", "connecticut", "new jersey", "new york", "north carolina",
    "south carolina", "alabama", "kentucky", "virginia", "vermont", "maine", "texas",
    "california", "florida", "ohio", "michigan", "illinois", "georgia", "tennessee",
    "colorado", "arizona", "oregon", "maryland",
    "australia", "new south wales", "queensland", "victoria", "tasmania",
    "western australia", "south australia", "new zealand", "south africa", "jamaica",
)
#: Two-letter (and a few longer) codes for the same: US states, Canadian
#: provinces, Australian states. Several are English words ("in", "or",
#: "me"), so a code only counts when it's all the part says, perhaps with a
#: zip or postal code ("PA 19601", "ON N6A 3K7").
_FOREIGN_CODES = frozenset("""
    al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne
    nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc
    on qc bc ab mb sk ns nb nl pe
    nsw vic qld tas nt act
    us
""".split())
_POSTAL_PIECE = re.compile(r"[a-z]?\d[a-z0-9]*")
#: A full UK postcode ("SW1A 1AA", "M1 1AE"); matched on lower-cased text.
_UK_POSTCODE = re.compile(r"(?<![a-z0-9])[a-z]{1,2}\d[a-z\d]? ?\d[a-z]{2}(?![a-z0-9])")
#: Words that say nothing about the country. A location made only of these
#: ("Remote", "Multiple locations", "Remote - EMEA") is kept: it might well
#: be UK, and the user can reject it, whereas a dropped role is never seen.
_VAGUE = {
    "remote", "hybrid", "home", "based", "working", "from", "multiple", "locations",
    "location", "various", "flexible", "nationwide", "anywhere", "office", "offices",
    "and", "or", "in", "any", "other", "several", "the", "emea", "europe", "european",
}
_WORDS = re.compile(r"[a-z0-9]+")
#: Separators between locations in a list ("London, UK; New York, NY").
_LIST_SEPARATORS = re.compile(r"[;|/\n]")
#: Foreign places whose names contain a UK one; blanked out before matching.
_LOOKALIKES = re.compile(r"new york|new south wales|new england|new hampshire")


@dataclass
class CheckResult:
    found: int        # jobs the site returned
    kept: int         # UK jobs that survived scoring's exclusions
    new: int          # rows actually inserted
    duplicates: int   # already in the queue
    error: str | None = None

    def as_dict(self) -> dict:
        return {"found": self.found, "kept": self.kept, "new": self.new,
                "duplicates": self.duplicates, "error": self.error}


# -- the network, in one place tests can replace ------------------------------

def _fetch(site: Any, search_terms: list[str]) -> list[dict]:
    """RawJobs from ``site`` (a ``careers.detect.CareersSite``). Imported
    lazily so this module, and its tests, don't need the careers adapters."""
    from ..careers.adapters import get_adapter
    from ..careers.net import check_url, make_client

    check_url(site.url)  # again: the address may resolve differently since it was added
    adapter = get_adapter(site.ats)

    async def run() -> list[dict]:
        async with make_client() as client:
            return await adapter(client, site, search_terms=search_terms)

    # wait_for rather than asyncio.timeout(): the latter needs Python 3.11.
    return asyncio.run(asyncio.wait_for(run(), CHECK_DEADLINE_SECONDS))


# -- mapping -----------------------------------------------------------------

def _contract_type(employment_type: str | None) -> str | None:
    """schema.org / ATS employment types onto the queue's job-type filter.
    Internships are the short fixed placements the "temp" filter is for;
    anything fixed-term or contracted is "contract"."""
    et = (employment_type or "").lower()
    if "intern" in et:
        return "temp"
    if any(w in et for w in ("contract", "temporary", "fixed")):
        return "contract"
    if any(w in et for w in ("full", "part", "permanent")):
        return "permanent"
    return None


def _posted(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def _money(raw: Any) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_posting(job: dict, *, employer: str) -> Posting | None:
    """A Posting from an adapter's RawJob, or None if it has no title or
    link to show. ``employer`` is the watched company's own name: the site
    rarely states it, and it's the one place it's certainly right."""
    title = " ".join(str(job.get("title") or "").split())
    url = str(job.get("url") or "").strip()
    if not title or not _is_web_link(url):
        return None
    return Posting(
        source="careers",
        source_id=hashlib.sha1(url.encode()).hexdigest()[:16],
        title=title,
        employer=employer,
        location=" ".join(str(job.get("location") or "").split()),
        description=str(job.get("description") or ""),
        url=url,
        posted=_posted(job.get("posted")),
        salary_min=_money(job.get("salary_min")),
        salary_max=_money(job.get("salary_max")),
        contract_type=_contract_type(job.get("employment_type")),
        via_agency=False,  # the employer's own site, by definition
    )


def _is_web_link(url: str) -> bool:
    """Only http(s) links reach the queue, which renders them to be clicked:
    a ``javascript:`` or relative link from a scraped page is no use there."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def _phrase(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])")


def _is_foreign_region(part: str) -> bool:
    """Whether one comma-separated part ("Ontario", "PA 19601") names a
    region or country abroad."""
    words = _WORDS.findall(part)
    if not words:
        return False
    joined = " ".join(words)
    if any(joined == n or joined.startswith(n + " ") for n in _FOREIGN_NAMES):
        return True
    return words[0] in _FOREIGN_CODES and all(_POSTAL_PIECE.fullmatch(w) for w in words[1:])


def _place_in_uk(entry: str, blanked: str, place: str) -> bool:
    """Whether ``place`` appears in one location ``entry`` without a
    foreign region after it. ``blanked`` is ``entry`` with lookalikes
    spaced out (same length, so positions line up)."""
    for m in _phrase(place).finditer(blanked):
        after = entry[m.end():].split(",")[1:]
        if not any(_is_foreign_region(part) for part in after):
            return True
    return False


def _entry_is_uk(entry: str, places: list[str]) -> bool | None:
    """True for a UK location, None if it says nothing about the country,
    False for anywhere else."""
    blanked = _LOOKALIKES.sub(lambda m: " " * len(m.group()), entry)
    if any(_phrase(m).search(blanked) for m in UK_COUNTRY_MARKERS):
        return True
    if _UK_POSTCODE.search(entry):
        return True
    if any(_place_in_uk(entry, blanked, p) for p in places):
        return True
    words = _WORDS.findall(blanked)
    if not words:  # nothing but a lookalike ("New York") is foreign; bare punctuation is unknown
        return None if not _WORDS.findall(entry) else False
    # "2 Locations", "Remote - EMEA": no country named.
    return None if all(w in _VAGUE or w.isdigit() for w in words) else False


def is_uk(location: str | None, places: Iterable[str] = ()) -> bool:
    """Whether to keep a role at ``location``: UK places (and the profile's
    own ``places``) yes, unknown or merely vague yes, anywhere else no.
    International firms list every office worldwide, so this does real
    work — a London scheme seeker doesn't want the New York intake, nor
    the London, Ontario one. A list of locations is kept if any is UK."""
    text = (location or "").lower()
    if not text.strip():
        return True
    towns = list(UK_PLACES) + [p.strip().lower() for p in places if p.strip()]
    verdicts = [_entry_is_uk(e, towns) for e in _LIST_SEPARATORS.split(text) if e.strip()]
    return not verdicts or any(v is not False for v in verdicts)


# -- the check ---------------------------------------------------------------

def search_terms(profile: Profile) -> list[str]:
    titles = profile.target_titles
    return sorted(titles, key=titles.get, reverse=True)[:MAX_SEARCH_TERMS]


def _describe(exc: BaseException) -> str:
    """A failure, in words for the user. Internals go to the log only."""
    from ..careers.net import ResponseTooLarge, UnsafeURL

    if isinstance(exc, UnsafeURL):
        return f"This address can't be used: {exc}."
    if isinstance(exc, ResponseTooLarge):
        return "The site sent more data than we can read."
    # asyncio's own TimeoutError is a separate class before Python 3.11.
    if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError, TimeoutError)):
        return f"The site took too long to answer. {_RETRY}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"The site answered with an error ({exc.response.status_code})."
    if isinstance(exc, httpx.HTTPError):
        return f"Couldn't reach the site. {_RETRY}"
    if isinstance(exc, psycopg.Error):
        log.exception("saving a careers check failed", exc_info=exc)
        return f"Couldn't save the jobs from this site. {_RETRY}"
    log.exception("careers check failed", exc_info=exc)
    return "Couldn't read jobs from this site. If it only shows jobs with JavaScript, " \
           "try its Workday, Greenhouse, Lever, Ashby or SmartRecruiters link instead."


#: Recorded when a check ends without a result (the request was cut off).
INTERRUPTED = f"The check was interrupted. {_RETRY}"


def _record(store: Store, cid: str, found: int, result: CheckResult | None) -> None:
    """Note the check on the company, however it ended. A failure here is
    logged, not raised: it mustn't hide the check's own result or error."""
    try:
        store.conn.rollback()  # a no-op unless a database error left the transaction aborted
        store.record_company_check(
            cid, found=found, new=result.new if result else 0,
            error=result.error if result else INTERRUPTED)
    except Exception:  # noqa: BLE001 — see docstring
        log.exception("couldn't record the check of company %s", cid)


def check_company(store: Store, company: dict) -> CheckResult:
    """Read one company's careers site and stage what matches. Always
    records the check: success, a failure of the site, or our own."""
    from ..careers.detect import CareersSite

    found = 0
    result: CheckResult | None = None
    try:
        profile = load_profile(store)
        site = CareersSite(ats=company["ats"], slug=company["slug"] or "",
                           url=company["careers_url"])
        jobs = _fetch(site, search_terms(profile))
        found = len(jobs)
        places = [p for p in profile.locations if p != LOCATION_ANYWHERE]
        postings = [p for p in (to_posting(j, employer=company["name"]) for j in jobs)
                    if p is not None and is_uk(p.location, places)]
        kept = score_all(postings, profile)
        new, dup = store.upsert(kept)
        result = CheckResult(found=found, kept=len(kept), new=new, duplicates=dup)
    except Exception as exc:  # noqa: BLE001 — anything the site does is reported, not raised
        result = CheckResult(found=found, kept=0, new=0, duplicates=0, error=_describe(exc))
    finally:
        _record(store, company["id"], found, result)
    return result
