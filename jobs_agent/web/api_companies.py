"""Companies endpoints: watching employers' own careers sites. Same
contract as api.py — plain functions of ``(store, request)``.

A check reads one company per request (see ``companies/pipeline.py``); the
queue page loops on ``/api/companies/check`` until ``remaining`` is 0.
Every check is load on someone else's site, so they're rationed: a
company checked minutes ago isn't read again (its last result is returned),
and each account has a daily budget of site checks.

Suggestions come from the AI (``/api/companies/suggest``, nothing saved);
accepting one calls ``/api/companies/find``, which reads the firm's website
for its careers site and adds it — one company per request, like checks.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from .. import llm
from ..careers.detect import detect
from ..careers.find import find_careers_site_sync
from ..careers.net import UnsafeURL, check_url
from ..companies import ats_label
from ..companies.catalog import suggested_for
from ..companies.pipeline import RECHECK_AFTER, check_company
from ..profile import load_profile
from ..storage import DOC_CV, Store
from ..storage.companies import MAX_COMPANIES, CompanyError
from ..suggest import SuggestError, suggest_companies
from .api import Json, Request, error

#: An explicit "Check now" this soon after the last check returns that
#: check's result instead of reading the site again: a double click, or a
#: user hoping something changed in five minutes, shouldn't cost the site.
COOLDOWN = timedelta(minutes=10)
#: Site checks one account may make per UTC day. 40 companies twice a day
#: (they're due every 12 hours) plus some "Check now"s; override with
#: JOBS_AGENT_COMPANY_CHECKS_PER_DAY.
DEFAULT_CHECKS_PER_DAY = 60
QUOTA_KIND = "company-check"
#: Careers-site searches one account may make per UTC day: each reads up
#: to ten pages of a firm's site. Override with
#: JOBS_AGENT_COMPANY_FINDS_PER_DAY.
DEFAULT_FINDS_PER_DAY = 30
FIND_QUOTA_KIND = "company-find"
MAX_NAME = 80
MAX_URL = 500


def _company(row) -> dict:
    out = {k: row[k] for k in row.keys() if k != "user_id"}
    out["ats_label"] = ats_label(row["ats"])
    out["host"] = urlsplit(row["careers_url"]).hostname or ""
    return out


def _due_before() -> str:
    # The same format CompanyStore stamps last_checked with, so TEXT order works.
    return (datetime.now(timezone.utc) - RECHECK_AFTER).isoformat(timespec="seconds")


def _daily_cap(env: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(env, "")))
    except ValueError:
        return default


def checks_per_day() -> int:
    return _daily_cap("JOBS_AGENT_COMPANY_CHECKS_PER_DAY", DEFAULT_CHECKS_PER_DAY)


def finds_per_day() -> int:
    return _daily_cap("JOBS_AGENT_COMPANY_FINDS_PER_DAY", DEFAULT_FINDS_PER_DAY)


def _checked_within(row, window: timedelta) -> bool:
    if not row["last_checked"]:
        return False
    try:
        then = datetime.fromisoformat(row["last_checked"])
    except ValueError:
        return False
    if then.tzinfo is None:  # an older naive stamp is UTC
        then = then.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - then < window


def _last_result(row) -> dict:
    """The stored outcome of the last check, shaped like CheckResult's.
    How many were kept or already seen isn't stored, so those are None."""
    return {"found": row["last_found"] or 0, "kept": None, "new": row["last_new"] or 0,
            "duplicates": None, "error": row["last_error"]}


def _next_due(store: Store) -> dict | None:
    row = store.next_company_to_check(due_before=_due_before())
    return {"id": row["id"], "name": row["name"]} if row else None


def get_companies(store: Store, req: Request) -> Json:
    rows = store.list_companies()
    return Json({
        "companies": [_company(r) for r in rows],
        "can_add": len(rows) < MAX_COMPANIES,
        "max": MAX_COMPANIES,
        "suggested": suggested_for(load_profile(store).preset, rows),
        "due": store.count_companies_due(_due_before()),
        "next_due": _next_due(store),
    })


def _text(payload: dict, field: str) -> str:
    value = payload.get(field)
    return " ".join(value.split()) if isinstance(value, str) else ""


def post_company(store: Store, req: Request) -> Json:
    """Add a company: ``{name, careers_url}``. The URL is checked for
    safety and its job-board software detected now, so a bad link is
    refused on the spot rather than failing silently at every check."""
    name = _text(req.payload, "name")
    url = _text(req.payload, "careers_url")
    if not name:
        return error("Give the company a name.")
    if len(name) > MAX_NAME:
        return error(f"Keep the name under {MAX_NAME} characters.")
    if not url:
        return error("Paste the link to the company's careers page.")
    if len(url) > MAX_URL:
        return error("That link is too long.")
    if "://" not in url and ":" not in url.split("/", 1)[0]:
        url = "https://" + url  # "www.firm.co.uk/careers", as people type it
    url = url.split("#", 1)[0]
    try:
        check_url(url)
    except UnsafeURL as e:
        return error(f"That link can't be used: {e}.")
    site = detect(url)
    try:
        row = store.add_company(name=name, careers_url=site.url, ats=site.ats, slug=site.slug)
    except CompanyError as e:
        return error(str(e))
    return Json({"company": _company(row)})


def post_company_delete(store: Store, req: Request) -> Json:
    cid = req.payload.get("id")
    if not isinstance(cid, str) or not cid:
        return error("id is required")
    if not store.delete_company(cid):
        return error("That company isn't on your list.", 404)
    return Json({"ok": True})


def post_company_check(store: Store, req: Request) -> Json:
    """Check one company: ``{id}``, or with no id the one most overdue.
    Returns its result and how many are still due, for the page's loop.
    A site's failure is a 200 carrying ``result.error``: the request did
    its job, the site didn't. An ``{id}`` checked within ``COOLDOWN``
    returns its last result with ``cooldown: true``; past the daily
    budget, 429."""
    with store.exclusive("companies") as mine:
        if not mine:
            return error("Company sites are already being checked in another tab.", 409)
        cid = req.payload.get("id")
        if cid:
            row = store.get_company(str(cid))
            if row is None:
                return error("That company isn't on your list.", 404)
        else:
            row = store.next_company_to_check(due_before=_due_before())
        if row is None:
            return Json({"company": None, "result": None, "remaining": 0, "next_due": None})
        if cid and _checked_within(row, COOLDOWN):
            minutes = int(COOLDOWN.total_seconds() // 60)
            return Json({
                "company": _company(row),
                "result": _last_result(row),
                "cooldown": True,
                "message": f"{row['name']} was checked in the last {minutes} minutes, so "
                           "here's what that check found. Try again in a few minutes.",
                "remaining": store.count_companies_due(_due_before()),
                "next_due": _next_due(store),
            })
        cap = checks_per_day()
        if not store.take_quota(QUOTA_KIND, cap):
            return error(f"You've used today's {cap} company site checks. "
                         "They start again tomorrow.", 429)
        result = check_company(store, row)
        return Json({
            "company": _company(store.get_company(row["id"])),
            "result": result.as_dict(),
            "remaining": store.count_companies_due(_due_before()),
            "next_due": _next_due(store),
        })


def post_company_suggest(store: Store, req: Request) -> Json:
    """Employers the AI thinks suit this profile and CV, minus the ones
    already watched: ``{suggestions: [{name, why, website}]}``. Nothing is
    saved; accepting one is a separate ``/api/companies/find``."""
    try:
        suggestions = suggest_companies(
            llm.for_user(store), load_profile(store), cv=store.get_document(DOC_CV),
            watched=[c["name"] for c in store.list_companies()])
    except SuggestError as e:
        return error(str(e))
    return Json({"suggestions": suggestions})


def post_company_find(store: Store, req: Request) -> Json:
    """Find ``{name, website}``'s careers site and watch it. Not finding one
    is a 200 with ``company: null`` and a message saying what to do instead;
    the request worked, the firm's site just didn't give anything up."""
    name = _text(req.payload, "name")
    website = _text(req.payload, "website")
    if not name:
        return error("Give the company a name.")
    if len(name) > MAX_NAME or len(website) > MAX_URL:
        return error("That name or website is too long.")
    if len(store.list_companies()) >= MAX_COMPANIES:
        return error(f"You can watch up to {MAX_COMPANIES} companies. Remove one to add another.")
    cap = finds_per_day()
    if not store.take_quota(FIND_QUOTA_KIND, cap):
        return error(f"You've used today's {cap} careers-site searches. "
                     "They start again tomorrow, or add the link by hand.", 429)
    found = find_careers_site_sync(name, website)
    if found is None:
        return Json({"company": None, "via": None,
                     "message": f"Couldn't find a careers site for {name}. Find their jobs "
                                "page and add the link by hand."})
    try:
        row = store.add_company(name=name, careers_url=found.site.url,
                                ats=found.site.ats, slug=found.site.slug)
    except CompanyError as e:
        return error(str(e))
    return Json({"company": _company(row), "via": found.via})
