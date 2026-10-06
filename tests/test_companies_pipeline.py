"""Checking one company's careers site: RawJobs become scored, stored
postings, non-UK roles are dropped, and every failure becomes a message.
The network is never touched — ``_fetch`` is replaced in every test."""

import asyncio
from dataclasses import replace
from datetime import date

import httpx
import pytest

from jobs_agent.companies import pipeline
from jobs_agent.companies.pipeline import check_company, is_uk, to_posting
from jobs_agent.presets import get_preset
from jobs_agent.profile import save_profile

PROFILE = replace(get_preset("accounting_graduate").profile, locations=["Reading"])


def raw(**kw):
    job = dict(title="Graduate Audit Associate", url="https://jobs.example.com/1",
               location="London, United Kingdom", employer=None,
               description="Join our audit graduate scheme with ACA study support.",
               posted="2026-10-01", salary_min=None, salary_max=None,
               employment_type="FULL_TIME")
    job.update(kw)
    return job


@pytest.fixture
def company(store):
    save_profile(store, PROFILE)
    return store.add_company(name="Example LLP", careers_url="https://boards.greenhouse.io/example",
                             ats="greenhouse", slug="example")


@pytest.fixture
def fetched(monkeypatch):
    """Set what the site 'returns'; records the search terms it was given."""
    calls = []

    def use(jobs):
        def fake(site, search_terms):
            calls.append({"site": site, "search_terms": search_terms})
            if isinstance(jobs, BaseException):
                raise jobs
            return jobs
        monkeypatch.setattr(pipeline, "_fetch", fake)
        return calls
    return use


# -- mapping ---------------------------------------------------------------

def test_a_raw_job_becomes_a_posting():
    p = to_posting(raw(salary_min=30000, salary_max="32000"), employer="Example LLP")
    assert p.source == "careers"
    assert len(p.source_id) == 16
    assert p.employer == "Example LLP"
    assert p.posted == date(2026, 10, 1)
    assert p.contract_type == "permanent"
    assert (p.salary_min, p.salary_max) == (30000.0, 32000.0)
    assert p.via_agency is False


@pytest.mark.parametrize("employment_type,expected", [
    ("FULL_TIME", "permanent"), ("Part-time", "permanent"), ("Permanent", "permanent"),
    ("CONTRACTOR", "contract"), ("Temporary", "contract"), ("Fixed Term", "contract"),
    ("INTERN", "temp"), ("Internship", "temp"), (None, None), ("", None), ("OTHER", None),
])
def test_contract_type_mapping(employment_type, expected):
    assert to_posting(raw(employment_type=employment_type), employer="X").contract_type == expected


@pytest.mark.parametrize("posted", [None, "", "not a date", "2026-13-45"])
def test_a_bad_posted_date_is_dropped(posted):
    assert to_posting(raw(posted=posted), employer="X").posted is None


def test_a_job_without_a_title_or_url_is_skipped():
    assert to_posting(raw(title="  "), employer="X") is None
    assert to_posting(raw(url=None), employer="X") is None


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "data:text/html,hi", "/jobs/1", "mailto:jobs@example.com",
    "ftp://example.com/job", "https://",
])
def test_a_job_whose_link_isnt_a_web_page_is_skipped(url):
    # The queue renders the link; only http(s) is safe and useful to click.
    assert to_posting(raw(url=url), employer="X") is None


def test_an_http_link_is_fine():
    assert to_posting(raw(url="HTTP://jobs.example.com/1"), employer="X") is not None


# -- the UK filter ---------------------------------------------------------

@pytest.mark.parametrize("location", [
    "", None, "London", "London, England, United Kingdom", "Remote - UK", "Manchester",
    "Edinburgh, Scotland", "GB", "Belfast", "Reading", "Remote", "Multiple locations",
    "Cardiff, Wales",
    # Vague: says nothing about the country, so the user decides.
    "2 Locations", "14 locations", "Hybrid", "Remote - EMEA", "Remote - Europe",
    "Remote - EMEA/Europe/UK", "  ",
    # Other ways of saying UK, and towns beyond the big cities.
    "GBR - Wolverhampton", "Great Britain", "Stirling, Scotland", "Perth, Scotland",
    "Stirling", "Wolverhampton", "Inverness", "Swindon",
    # A postcode is as UK as it gets.
    "Unit 4, SW1A 1AA", "EC2A 4NE", "Office: M1 1AE",
    # A UK place in a multi-location list with a foreign one.
    "London, United Kingdom; New York, NY", "Toronto, ON | London, UK",
    # A UK city whose next part is UK, not foreign.
    "Reading, Berkshire", "London, SW1A 1AA",
])
def test_uk_and_unknown_locations_are_kept(location):
    assert is_uk(location, ["Reading"])


@pytest.mark.parametrize("location", [
    "New York, NY", "New York", "Paris, France", "Bangalore, India", "Kyiv, Ukraine", "Remote - US",
    "Dublin, Ireland",
    # UK place names abroad: the region or country after the comma decides.
    "London, Ontario", "London, ON", "London, ON N6A 3K7", "London, Ontario, Canada",
    "London, Canada", "Newcastle, NSW", "Newcastle, New South Wales", "Perth, Australia",
    "Perth, WA", "Reading, PA", "Reading, PA 19601", "Birmingham, AL", "Cambridge, MA",
    "Cambridge, Massachusetts, United States", "Manchester, NH", "Bristol, CT",
    "York, USA", "Durham, NC", "Plymouth, MA",
    # Crown dependencies aren't the UK for visas or tax.
    "St Helier, Jersey", "Guernsey", "Douglas, Isle of Man",
    # Perth on its own is far more often Australia.
    "Perth",
])
def test_clearly_foreign_locations_are_dropped(location):
    assert not is_uk(location, ["Reading"])


# -- the whole check -------------------------------------------------------

def test_check_scores_stores_and_records(store, company, fetched):
    calls = fetched([
        raw(),
        raw(title="Graduate Audit Associate", url="https://jobs.example.com/2",
            location="New York, NY", description="US audit team."),
        raw(title="Head of Audit", url="https://jobs.example.com/3", description="Lead it."),
    ])
    result = check_company(store, company)
    assert (result.found, result.kept, result.new, result.error) == (3, 1, 1, None)
    # Strongest target titles first, at most six of them.
    terms = calls[0]["search_terms"]
    assert len(terms) == 6
    weights = [PROFILE.target_titles[t] for t in terms]
    assert weights == sorted(weights, reverse=True)
    assert calls[0]["site"].ats == "greenhouse" and calls[0]["site"].slug == "example"

    rows = list(store.queue())
    assert len(rows) == 1
    assert rows[0]["source"] == "careers" and rows[0]["employer"] == "Example LLP"
    assert rows[0]["score"] > 0

    row = store.get_company(company["id"])
    assert (row["last_found"], row["last_new"], row["last_error"]) == (3, 1, None)
    assert row["last_checked"]


def test_a_second_check_finds_nothing_new(store, company, fetched):
    fetched([raw()])
    check_company(store, company)
    result = check_company(store, company)
    assert (result.new, result.duplicates) == (0, 1)


@pytest.mark.parametrize("exc,words", [
    (httpx.ConnectTimeout("slow"), "too long"),
    (httpx.ConnectError("nope"), "reach"),
    (httpx.HTTPStatusError("bad", request=httpx.Request("GET", "https://x.com"),
                           response=httpx.Response(503)), "503"),
    (RuntimeError("adapter blew up"), "Couldn't read"),
])
def test_failures_become_a_recorded_message(store, company, fetched, exc, words):
    fetched(exc)
    result = check_company(store, company)
    assert result.error and words in result.error
    assert "adapter blew up" not in result.error  # internals stay in the log
    assert (result.found, result.new) == (0, 0)
    assert store.get_company(company["id"])["last_error"] == result.error


def test_a_timeout_says_when_it_will_be_retried(store, company, fetched):
    fetched(asyncio.TimeoutError())
    result = check_company(store, company)
    assert "took too long" in result.error
    assert "12 hours" in result.error and "Check now" in result.error
    assert store.get_company(company["id"])["last_error"] == result.error


def test_a_reach_failure_says_when_it_will_be_retried(store, company, fetched):
    fetched(httpx.ConnectError("nope"))
    error = check_company(store, company).error
    assert "next time" not in error and "12 hours" in error and "Check now" in error


def test_the_whole_site_read_has_a_deadline(store, company, monkeypatch):
    """A site trickling bytes can dodge every per-request timeout; the
    overall deadline stops it holding the request open."""
    from jobs_agent.careers import adapters, net

    async def stalls(client, site, search_terms):
        await asyncio.sleep(10)
        return []

    monkeypatch.setattr(net, "check_url", lambda url: url)
    monkeypatch.setattr(adapters, "get_adapter", lambda ats: stalls)
    monkeypatch.setattr(pipeline, "CHECK_DEADLINE_SECONDS", 0.05)
    result = check_company(store, company)
    assert "took too long" in result.error
    assert store.get_company(company["id"])["last_checked"]


def test_a_scoring_failure_is_still_recorded(store, company, fetched, monkeypatch):
    fetched([raw()])

    def broken(postings, profile):
        raise RuntimeError("scoring blew up")
    monkeypatch.setattr(pipeline, "score_all", broken)
    result = check_company(store, company)
    assert result.error and "blew up" not in result.error
    row = store.get_company(company["id"])
    assert row["last_checked"] and row["last_error"] == result.error
    assert row["last_found"] == 1


def test_a_database_failure_while_saving_is_still_recorded(store, company, fetched, monkeypatch):
    fetched([raw()])

    def broken(postings):
        store.conn.execute("SELECT * FROM no_such_table")  # leaves the transaction aborted
    monkeypatch.setattr(store, "upsert", broken)
    result = check_company(store, company)
    assert result.error and "no_such_table" not in result.error
    row = store.get_company(company["id"])
    assert row["last_checked"] and row["last_error"] == result.error


def test_an_interrupted_check_is_still_recorded(store, company, fetched):
    fetched(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        check_company(store, company)
    row = store.get_company(company["id"])
    assert row["last_checked"] and row["last_error"]


def test_an_oversized_response_is_explained(store, company, fetched):
    from jobs_agent.careers.net import ResponseTooLarge

    fetched(ResponseTooLarge("12 MB"))
    assert "more data than we can read" in check_company(store, company).error


def test_the_deadline_outlasts_one_slow_fetch():
    # One careers fetch can take ~35s (its own deadline plus a retry).
    assert pipeline.CHECK_DEADLINE_SECONDS > 35


def test_an_unsafe_address_is_explained(store, company, fetched):
    from jobs_agent.careers.net import UnsafeURL

    fetched(UnsafeURL("private address"))
    assert "can't be used" in check_company(store, company).error


def test_as_dict(store, company, fetched):
    fetched([])
    body = check_company(store, company).as_dict()
    assert body == {"found": 0, "kept": 0, "new": 0, "duplicates": 0, "error": None}
