"""Pinpoint boards and plain JSON job indexes turn into RawJob dicts.

Fixtures are trimmed copies of real responses (Menzies on Pinpoint, RSM UK's
Adobe Edge Delivery job index, October 2026). No test touches the network:
requests go to an httpx.MockTransport through the SSRF-checked client.
"""

import asyncio

import httpx
import pytest

from jobs_agent.careers import net
from jobs_agent.careers.adapters import ADAPTERS, RAW_JOB_KEYS, json_index, pinpoint
from jobs_agent.careers.detect import CareersSite, detect
from jobs_agent.companies import ats_label

TERMS = ["graduate accountant", "audit"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(net, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(net, "_sleep", no_sleep)


def run(adapter, site, handler, terms=TERMS, max_jobs=60):
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    async def go():
        async with net.make_client(transport=httpx.MockTransport(record)) as client:
            return await adapter(client, site, search_terms=terms, max_jobs=max_jobs)
    return asyncio.run(go()), seen


def test_registry_and_labels():
    assert ADAPTERS["pinpoint"] is pinpoint.fetch_jobs
    assert ADAPTERS["json-index"] is json_index.fetch_jobs
    assert ats_label("pinpoint") == "Pinpoint"
    assert ats_label("json-index") == "Job index"


# --- Pinpoint -------------------------------------------------------------------

def _pinpoint_posting(**overrides):
    posting = {
        "title": "Graduate Programme 2027 - ACA Audit & Assurance ",
        "url": "https://menzies.pinpointhq.com/en/postings/f81bdb8b-0d5f-4f5e-ae99-ebf3a6322dcd",
        "description": "<div><!--block--><em>Menzies is an equal opportunities employer"
                       "</em></div>",
        "key_responsibilities_header": "What does our Audit & Assurance team do?",
        "key_responsibilities": "<div><!--block-->Our Audit &amp; Assurance teams carry out "
                                "a detailed, risk-based review</div>",
        "skills_knowledge_expertise_header": "What do we look for? ",
        "skills_knowledge_expertise": "<div>✔️ A minimum of a 2:2 undergraduate degree"
                                      "<br>✔️ Collaborative</div>",
        "benefits_header": "Job Benefits",
        "benefits": "<div>Study support towards the ACA</div>",
        "employment_type": "fixed_term_contract",
        "employment_type_text": "Fixed Term Contract",
        "compensation_minimum": 29000.0, "compensation_maximum": 29000.0,
        "compensation_currency": "GBP", "compensation_frequency": "year",
        "compensation_visible": True,
        "deadline_at": None,
        "location": {"id": "55907", "city": "Woking", "name": "Woking Office",
                     "postal_code": "GU21 6LQ", "province": "UK"},
        "job": {"id": "555807", "department": {"id": "60513", "name": "Audit & Assurance"},
                "division": {"id": "11875", "name": "Early Careers"}},
    }
    posting.update(overrides)
    return posting


PINPOINT = {"data": [
    _pinpoint_posting(),
    _pinpoint_posting(
        title="Private Client Tax Director",
        url="https://menzies.pinpointhq.com/en/postings/68f11550",
        employment_type_text="Full Time", compensation_visible=False,
        compensation_minimum=None, compensation_maximum=None,
        location={"city": "Fareham", "name": "Solent Office", "postal_code": "PO15 7FX",
                  "province": "uk"},
        job={"department": {"name": "Tax"}, "division": {"name": "Professional"}}),
    # Not a graduate title, but the "Early Careers" division says it is one.
    _pinpoint_posting(title="Tax Associate 2027", url="https://menzies.pinpointhq.com/en/postings/3",
                      compensation_currency="USD"),
]}


def test_pinpoint_maps_and_filters():
    def handler(request):
        assert str(request.url) == "https://menzies.pinpointhq.com/postings.json"
        return httpx.Response(200, json=PINPOINT)

    jobs, seen = run(pinpoint.fetch_jobs, CareersSite("pinpoint", "menzies", ""), handler)
    assert len(seen) == 1
    assert [j["title"] for j in jobs] == ["Graduate Programme 2027 - ACA Audit & Assurance",
                                          "Tax Associate 2027"]
    for job in jobs:
        assert tuple(job) == RAW_JOB_KEYS and job["employer"] is None
    first, second = jobs
    assert first["url"] == PINPOINT["data"][0]["url"]
    assert first["location"] == "Woking, GU21 6LQ"
    assert first["employment_type"] == "Fixed Term Contract"
    assert (first["salary_min"], first["salary_max"]) == (29000.0, 29000.0)
    assert first["posted"] is None  # Pinpoint publishes no posting date
    description = first["description"]
    assert "<" not in description and "&amp;" not in description
    assert (description.index("What does our Audit & Assurance team do?")
            < description.index("risk-based review")
            < description.index("2:2 undergraduate degree")
            < description.index("Study support"))
    assert (second["salary_min"], second["salary_max"]) == (None, None)  # USD ignored


def test_pinpoint_hidden_or_hourly_pay_is_not_reported():
    board = {"data": [
        _pinpoint_posting(compensation_visible=False),
        _pinpoint_posting(url="https://x/2", compensation_frequency="hour",
                          compensation_minimum=12.5, compensation_maximum=13.0),
    ]}
    jobs, _ = run(pinpoint.fetch_jobs, CareersSite("pinpoint", "menzies", ""),
                  lambda r: httpx.Response(200, json=board))
    assert [(j["salary_min"], j["salary_max"]) for j in jobs] == [(None, None), (None, None)]


def test_pinpoint_puts_uk_roles_first_before_capping():
    board = {"data": [_pinpoint_posting(url=f"https://x/{i}", title=f"Graduate {i}",
                                        location={"city": "Dublin", "province": "Leinster"})
                      for i in range(4)]
             + [_pinpoint_posting(url="https://x/uk", title="Graduate UK",
                                  location={"city": "Leeds", "province": "uk"})]}
    jobs, _ = run(pinpoint.fetch_jobs, CareersSite("pinpoint", "menzies", ""),
                  lambda r: httpx.Response(200, json=board), max_jobs=2)
    assert [j["title"] for j in jobs] == ["Graduate UK", "Graduate 0"]


def test_pinpoint_refuses_a_slug_that_is_not_a_hostname_label():
    with pytest.raises(ValueError):
        run(pinpoint.fetch_jobs, CareersSite("pinpoint", "evil.example/x", ""),
            lambda r: httpx.Response(200, json={"data": []}))


# --- JSON job index -----------------------------------------------------------------

RSM_URL = "https://www.rsmuk.com/job-search-index.json"
RSM = {
    "total": 3, "offset": 0, "limit": 3, ":type": "sheet",
    "columns": ["path", "jobId", "jobTitle", "jobLocation", "roleType", "applyLink",
                "jobCreatedAt", "jobDescription"],
    "data": [
        {"path": "/careers/jobs/ec104", "jobId": "EC104",
         "jobTitle": "Audit Graduate - Edinburgh - September 2027",
         "jobLocation": "offices:edinburgh", "roleType": "jobs:role-type/graduate",
         "serviceLine": "jobs:service-line/audit", "jobTime": "Full time",
         "jobDescription": "",
         "applyLink": "https://rsm-careers.tal.net/vx/appcentre-ext/candidate/so/pm/1/pl/2/opp/104",
         "jobCreatedAt": 1788220800, "lastModified": 1788251734},
        {"path": "/careers/jobs/ehjr100009", "jobId": "EHJR100009",
         "jobTitle": "Ethics and Independence Director",
         "jobLocation": "offices:london,offices:leeds",
         "roleType": "jobs:role-type/experienced-hire", "jobDescription": "",
         "applyLink": "https://rsmuk.wd103.myworkdayjobs.com/RSM/job/London/x",
         "jobCreatedAt": 0, "lastModified": 1787814109},
        # No entry-level word in the title; the role type says it is one.
        {"path": "/careers/jobs/ec12", "jobTitle": "Tax Associate - September 2027",
         "jobLocation": "offices:milton-keynes,offices:london",
         "roleType": "jobs:role-type/placement", "jobDescription": "<p>Learn tax.</p>",
         "applyLink": "", "jobCreatedAt": 0},
    ],
}


def test_json_index_reads_an_edge_delivery_index():
    def handler(request):
        assert str(request.url) == RSM_URL
        return httpx.Response(200, json=RSM)

    site = detect(RSM_URL)
    jobs, seen = run(json_index.fetch_jobs, site, handler, terms=["tax"])
    assert len(seen) == 1
    assert [j["title"] for j in jobs] == ["Audit Graduate - Edinburgh - September 2027",
                                          "Tax Associate - September 2027"]
    first, second = jobs
    assert tuple(first) == RAW_JOB_KEYS and first["employer"] is None
    # The firm's own job page, resolved against the index's host.
    assert first["url"] == "https://www.rsmuk.com/careers/jobs/ec104"
    assert first["location"] == "Edinburgh"
    assert first["posted"] == "2026-09-01"  # epoch seconds
    assert second["location"] == "Milton Keynes, London"
    assert second["posted"] is None         # 0 is "unknown", not 1970
    assert second["description"] == "Learn tax."


def test_json_index_relevance_uses_the_role_type_when_the_title_says_nothing():
    jobs, _ = run(json_index.fetch_jobs, detect(RSM_URL),
                  lambda r: httpx.Response(200, json=RSM), terms=[])
    assert [j["title"] for j in jobs] == ["Audit Graduate - Edinburgh - September 2027",
                                          "Tax Associate - September 2027"]


def test_json_index_accepts_a_bare_list_and_common_key_names():
    index = [
        {"title": "Graduate Accountant", "location": "Bristol, UK",
         "url": "https://firm.example/jobs/1", "created": 1788220800000,
         "summary": "ACA study support"},
        {"name": "Trainee Auditor", "city": "Leeds", "link": "/jobs/2",
         "date": "2026-09-30T09:00:00Z"},
        {"jobTitle": "Audit Apprentice", "applyLink": "https://apply.example/3"},
        {"title": "", "url": "/jobs/4"},           # no title: skipped
        "not a job",                               # junk rows are ignored
    ]
    site = CareersSite("json-index", "firm.example", "https://firm.example/feeds/jobs.json")
    jobs, _ = run(json_index.fetch_jobs, site, lambda r: httpx.Response(200, json=index))
    assert [j["title"] for j in jobs] == ["Graduate Accountant", "Trainee Auditor",
                                          "Audit Apprentice"]
    one, two, three = jobs
    assert one["posted"] == "2026-09-01" and one["description"] == "ACA study support"
    assert one["location"] == "Bristol, UK"
    assert two["url"] == "https://firm.example/jobs/2"
    assert two["location"] == "Leeds" and two["posted"] == "2026-09-30"
    assert three["url"] == "https://apply.example/3"


def test_json_index_caps_and_puts_uk_first():
    index = {"data": [{"title": f"Graduate {i}", "location": "New York",
                       "url": f"/jobs/{i}"} for i in range(5)]
             + [{"title": "Graduate UK", "location": "London", "url": "/jobs/uk"}]}
    site = CareersSite("json-index", "firm.example", "https://firm.example/jobs.json")
    jobs, _ = run(json_index.fetch_jobs, site, lambda r: httpx.Response(200, json=index),
                  max_jobs=2)
    assert [j["title"] for j in jobs] == ["Graduate UK", "Graduate 0"]


def test_json_index_that_is_not_a_job_list_raises():
    site = CareersSite("json-index", "firm.example", "https://firm.example/jobs.json")
    with pytest.raises(ValueError):
        run(json_index.fetch_jobs, site, lambda r: httpx.Response(200, json={"ok": True}))
