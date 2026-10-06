"""Jooble and Careerjet adapters, and which boards ``build_sources`` builds.

Every response here comes from ``httpx.MockTransport``; nothing touches the
network.
"""

import asyncio
import json
from datetime import date

import httpx
import pytest

from jobs_agent.sources import (
    CareerjetSource,
    JoobleSource,
    NoSourcesConfigured,
    ReedSource,
    build_sources,
)
from jobs_agent.sources.jooble import JoobleError

BOARD_ENV = ("REED_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
             "JOOBLE_API_KEY", "CAREERJET_API_KEY", "CAREERJET_USER_IP")


def run(coro):
    return asyncio.run(coro)


def serve(*pages):
    """An AsyncClient answering with ``pages`` in turn (the last repeats),
    recording every request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=pages[min(len(seen), len(pages)) - 1])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# -- Jooble -----------------------------------------------------------------

JOOBLE_JOB = {
    "id": 4242,
    "title": "Graduate <b>Accountant</b>",
    "location": "London",
    "snippet": "&nbsp;Join our <b>audit</b> team with ACA study support...",
    "salary": "£28,000 - £32,000",
    "source": "totaljobs.com",
    "type": "Permanent",
    "link": "https://uk.jooble.org/desc/4242",
    "company": "Acme LLP",
    "updated": "2026-09-15T12:55:35.3870000",
}


def test_jooble_posts_keyword_location_and_radius_to_the_keyed_url():
    client, seen = serve({"totalCount": 0, "jobs": []})
    run(JoobleSource("secret").fetch(client, "audit trainee", 10,
                                     location="Leeds", radius_miles=10))
    req = seen[0]
    assert req.method == "POST"
    assert req.url.path.endswith("/api/secret")
    sent = body(req)
    assert sent["keywords"] == "audit trainee"
    assert sent["location"] == "Leeds"
    assert sent["radius"] == "16"  # 10 miles -> 16 km, one of Jooble's steps
    assert sent["page"] == 1


@pytest.mark.parametrize("miles,km", [(0, "0"), (2, "4"), (15, "26"), (30, "80"), (100, "80")])
def test_jooble_rounds_the_radius_up_to_its_allowed_steps(miles, km):
    client, seen = serve({"totalCount": 0, "jobs": []})
    run(JoobleSource("k").fetch(client, "x", 10, location="Leeds", radius_miles=miles))
    assert body(seen[0])["radius"] == km


def test_jooble_searches_the_whole_uk_when_the_location_is_anywhere():
    client, seen = serve({"totalCount": 0, "jobs": []})
    run(JoobleSource("k").fetch(client, "x", 10, location="UK", radius_miles=10))
    sent = body(seen[0])
    assert sent["location"] == "United Kingdom"
    assert "radius" not in sent


def test_jooble_maps_a_job_to_a_posting():
    client, _ = serve({"totalCount": 1, "jobs": [JOOBLE_JOB]})
    [p] = run(JoobleSource("k").fetch(client, "x", 10, location="London", radius_miles=10))
    assert p.source == "jooble"
    assert p.source_id == "4242"
    assert p.title == "Graduate Accountant"
    assert p.employer == "Acme LLP"
    assert p.location == "London"
    assert "<b>" not in p.description and "audit" in p.description
    assert "&nbsp;" not in p.description
    assert p.url == "https://uk.jooble.org/desc/4242"
    assert p.posted == date(2026, 9, 15)
    assert p.contract_type == "permanent"
    # Jooble's salary is free text, so it isn't guessed at.
    assert p.salary_min is None and p.salary_max is None


def test_jooble_pages_until_it_has_the_total():
    page1 = {"totalCount": 3, "jobs": [dict(JOOBLE_JOB, id=1), dict(JOOBLE_JOB, id=2)]}
    page2 = {"totalCount": 3, "jobs": [dict(JOOBLE_JOB, id=3)]}
    client, seen = serve(page1, page2)
    out = run(JoobleSource("k").fetch(client, "x", 100, location="London", radius_miles=10))
    assert [p.source_id for p in out] == ["1", "2", "3"]
    assert [body(r)["page"] for r in seen] == [1, 2]


def test_jooble_stops_on_an_empty_page():
    client, seen = serve({"totalCount": 50, "jobs": [JOOBLE_JOB]}, {"totalCount": 50, "jobs": []})
    out = run(JoobleSource("k").fetch(client, "x", 100, location="London", radius_miles=10))
    assert len(out) == 1 and len(seen) == 2


def test_jooble_errors_do_not_leak_the_key_in_the_url():
    def handler(request):
        return httpx.Response(403, text="forbidden")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(Exception) as err:
        run(JoobleSource("topsecret").fetch(client, "x", 10, location="London", radius_miles=5))
    assert "topsecret" not in str(err.value)


@pytest.mark.parametrize("exc", [httpx.InvalidURL, RuntimeError, OSError])
def test_any_jooble_failure_hides_the_key(exc):
    """Not just HTTP errors: an invalid-URL error, or anything else the
    client raises, quotes the URL — and the key is in the URL."""
    def handler(request):
        raise exc(f"bad request to {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(JoobleError) as err:
        run(JoobleSource("topsecret").fetch(client, "x", 10, location="London", radius_miles=5))
    assert "topsecret" not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_a_jooble_and_a_reed_copy_of_one_job_share_a_dedupe_key():
    jooble = JoobleSource._to_posting(JOOBLE_JOB)
    reed = ReedSource._to_posting({
        "jobId": 99, "jobTitle": "Graduate Accountant", "employerName": "Acme LLP",
        "locationName": "London",
        "jobDescription": "Join our audit team with ACA study support...",
        "jobUrl": "https://www.reed.co.uk/jobs/99", "date": "15/09/2026",
    })
    assert jooble.source != reed.source
    assert jooble.key == reed.key


# -- Careerjet ----------------------------------------------------------------

CAREERJET_JOB = {
    "title": "Trainee <b>Accountant</b>",
    "company": "Bean & Co",
    "date": "Tue, 15 Sep 2026 19:13:43 GMT",
    "description": "Study towards the <b>ACCA</b> &amp; grow.",
    "locations": "Manchester, Greater Manchester",
    "salary": "£25,000 - £28,000 per annum",
    "salary_currency_code": "GBP",
    "salary_min": 25000.0,
    "salary_max": 28000.0,
    "salary_type": "Y",
    "site": "www.example-agency.co.uk",
    "url": "https://jobviewtrack.com/en-gb/job-abc123/",
}


def careerjet_page(jobs, pages=1):
    return {"type": "JOBS", "hits": len(jobs), "pages": pages, "jobs": jobs}


def test_careerjet_sends_keyword_location_radius_and_user_details():
    client, seen = serve(careerjet_page([]))
    run(CareerjetSource("ck", user_ip="203.0.113.7").fetch(
        client, "audit trainee", 10, location="Leeds", radius_miles=10))
    req = seen[0]
    assert req.url.host == "search.api.careerjet.net"
    assert req.url.path == "/v4/query"
    params = req.url.params
    assert params["keywords"] == "audit trainee"
    assert params["location"] == "Leeds"
    assert params["radius"] == "10"
    assert params["locale_code"] == "en_GB"
    assert params["user_ip"] == "203.0.113.7"
    assert params["user_agent"]
    assert req.headers["authorization"].startswith("Basic ")


def test_careerjet_searches_the_whole_uk_when_the_location_is_anywhere():
    client, seen = serve(careerjet_page([]))
    run(CareerjetSource("ck").fetch(client, "x", 10, location="UK", radius_miles=10))
    params = seen[0].url.params
    assert "location" not in params and "radius" not in params


def test_careerjet_maps_a_job_to_a_posting():
    client, _ = serve(careerjet_page([CAREERJET_JOB]))
    [p] = run(CareerjetSource("ck").fetch(client, "x", 10, location="Manchester",
                                          radius_miles=10))
    assert p.source == "careerjet"
    assert p.source_id == CAREERJET_JOB["url"]
    assert p.title == "Trainee Accountant"
    assert p.employer == "Bean & Co"
    assert p.location == "Manchester, Greater Manchester"
    assert " ".join(p.description.split()) == "Study towards the ACCA & grow."
    assert p.posted == date(2026, 9, 15)
    assert (p.salary_min, p.salary_max) == (25000.0, 28000.0)


@pytest.mark.parametrize("change", [
    {"salary_type": "H"},              # hourly — scoring compares annual pay
    {"salary_currency_code": "EUR"},
    {"salary_min": None, "salary_max": None},
])
def test_careerjet_leaves_non_annual_or_missing_salaries_unset(change):
    p = CareerjetSource._to_posting(dict(CAREERJET_JOB, **change))
    assert p.salary_min is None and p.salary_max is None


def test_careerjet_pages_until_the_last_page():
    client, seen = serve(careerjet_page([CAREERJET_JOB], pages=2),
                         careerjet_page([dict(CAREERJET_JOB, url="https://x/2")], pages=2))
    out = run(CareerjetSource("ck").fetch(client, "x", 100, location="Leeds", radius_miles=5))
    assert len(out) == 2
    assert [r.url.params["page"] for r in seen] == ["1", "2"]


def test_careerjet_treats_an_ambiguous_location_as_no_results():
    client, seen = serve({"type": "LOCATIONS", "locations": ["Newport, Wales", "Newport, IoW"]})
    out = run(CareerjetSource("ck").fetch(client, "x", 100, location="Newport", radius_miles=5))
    assert out == [] and len(seen) == 1


# -- build_sources ------------------------------------------------------------

@pytest.fixture
def no_board_keys(monkeypatch):
    for var in BOARD_ENV:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_build_sources_warns_about_each_missing_board(no_board_keys):
    no_board_keys.setenv("REED_API_KEY", "r")
    sources, warnings = build_sources()
    assert [s.name for s in sources] == ["reed"]
    joined = " ".join(warnings)
    assert "JOOBLE_API_KEY" in joined and "CAREERJET_API_KEY" in joined


def test_build_sources_builds_jooble_and_careerjet_when_keyed(no_board_keys):
    no_board_keys.setenv("JOOBLE_API_KEY", "j")
    no_board_keys.setenv("CAREERJET_API_KEY", "c")
    sources, _ = build_sources()
    assert {s.name for s in sources} == {"jooble", "careerjet"}


def test_build_sources_passes_the_job_category_to_adzuna(no_board_keys):
    no_board_keys.setenv("ADZUNA_APP_ID", "i")
    no_board_keys.setenv("ADZUNA_APP_KEY", "k")
    [adzuna], _ = build_sources(job_category="legal")
    assert adzuna.category == "legal-jobs"


def test_build_sources_with_no_keys_at_all_raises(no_board_keys):
    with pytest.raises(NoSourcesConfigured, match="JOOBLE_API_KEY"):
        build_sources()
