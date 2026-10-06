"""AI company suggestions and finding their careers sites, through the
API. The AI and the web are both stubbed."""

import json

import pytest

from jobs_agent.careers.detect import detect
from jobs_agent.careers.find import Found
from jobs_agent.llm import LLMError
from jobs_agent.presets import get_preset
from jobs_agent.profile import save_profile
from jobs_agent.storage import DOC_CV
from jobs_agent.web import api, api_companies, handler


def req(**payload):
    return api.Request(payload=payload)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr("jobs_agent.careers.net._resolve", lambda host: ["93.184.216.34"])


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, system, messages, **kwargs):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def ai(monkeypatch):
    holder = {}

    def use(reply):
        holder["client"] = FakeClient(reply)
        return holder["client"]
    monkeypatch.setattr(api_companies.llm, "for_user", lambda store, fast=False: holder["client"])
    return use


@pytest.fixture
def finder(monkeypatch):
    """What finding a careers site 'returns', and the names it was asked for."""
    holder = {"found": None, "asked": []}

    def fake(name, website):
        holder["asked"].append((name, website))
        return holder["found"]
    monkeypatch.setattr(api_companies, "find_careers_site_sync", fake)
    return holder


# -- suggesting ----------------------------------------------------------------

def test_suggestions_use_the_profile_and_cv_and_skip_watched_firms(store, ai):
    save_profile(store, get_preset("paralegal_london").profile)
    store.set_document(DOC_CV, "LLB Law graduate.")
    api_companies.post_company(store, req(name="DWF", careers_url="https://boards.greenhouse.io/dwf"))
    client = ai(json.dumps({"companies": [
        {"name": "DWF", "why": "x", "website": "dwfgroup.com"},
        {"name": "Kennedys", "why": "Insurance litigation.", "website": "kennedyslaw.com"},
    ]}))
    res = api_companies.post_company_suggest(store, req())
    assert res.status == 200
    assert res.body["suggestions"] == [
        {"name": "Kennedys", "why": "Insurance litigation.", "website": "kennedyslaw.com"}]
    prompt = client.calls[0][0].content
    assert "LLB Law graduate." in prompt and "DWF" in prompt


def test_a_suggestion_failure_is_a_400_with_the_reason(store, ai):
    save_profile(store, get_preset("paralegal_london").profile)
    ai(LLMError("No AI provider is set up."))
    res = api_companies.post_company_suggest(store, req())
    assert res.status == 400
    assert "No AI provider" in res.body["error"]


# -- finding and adding ----------------------------------------------------------

def test_a_found_site_is_added_to_the_watchlist(store, finder):
    finder["found"] = Found(site=detect("https://kennedys.wd3.myworkdayjobs.com/Graduates"),
                            via="job board")
    res = api_companies.post_company_find(store, req(name="Kennedys", website="kennedyslaw.com"))
    assert res.status == 200
    assert finder["asked"] == [("Kennedys", "kennedyslaw.com")]
    assert res.body["via"] == "job board"
    assert res.body["company"]["ats"] == "workday"
    assert [c["name"] for c in store.list_companies()] == ["Kennedys"]


def test_a_site_that_cannot_be_found_is_a_200_saying_so(store, finder):
    res = api_companies.post_company_find(store, req(name="Tiny Firm", website="tiny.co.uk"))
    assert res.status == 200
    assert res.body["company"] is None
    assert "Tiny Firm" in res.body["message"]
    assert store.list_companies() == []


def test_a_found_site_already_watched_says_so(store, finder):
    api_companies.post_company(store, req(name="Monzo", careers_url="https://boards.greenhouse.io/monzo"))
    finder["found"] = Found(site=detect("https://boards.greenhouse.io/monzo"), via="job board")
    res = api_companies.post_company_find(store, req(name="Monzo Bank", website="monzo.com"))
    assert res.status == 400
    assert "already" in res.body["error"]


def test_finding_needs_a_name(store, finder):
    assert api_companies.post_company_find(store, req(website="x.com")).status == 400
    assert finder["asked"] == []


def test_a_full_watchlist_is_refused_before_any_site_is_read(store, finder, monkeypatch):
    monkeypatch.setattr(api_companies, "MAX_COMPANIES", 0)
    res = api_companies.post_company_find(store, req(name="Kennedys", website="kennedyslaw.com"))
    assert res.status == 400
    assert finder["asked"] == []


def test_finds_are_capped_per_day(store, finder, monkeypatch):
    monkeypatch.setenv("JOBS_AGENT_COMPANY_FINDS_PER_DAY", "1")
    assert api_companies.post_company_find(store, req(name="A", website="a.com")).status == 200
    res = api_companies.post_company_find(store, req(name="B", website="b.com"))
    assert res.status == 429 and "tomorrow" in res.body["error"]
    assert len(finder["asked"]) == 1


def test_routes_are_registered():
    assert handler.POST_ROUTES["/api/companies/suggest"] is api_companies.post_company_suggest
    assert handler.POST_ROUTES["/api/companies/find"] is api_companies.post_company_find
