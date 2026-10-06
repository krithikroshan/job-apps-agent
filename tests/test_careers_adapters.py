"""ATS adapters turn each system's JSON into RawJob dicts.

Fixtures are trimmed copies of real responses (Monzo on Greenhouse, Palantir
on Lever, Multiverse/Ramp on Ashby, Bosch on SmartRecruiters, PwC on
Workday, October 2026). No test touches the network: requests go to an
httpx.MockTransport through the same SSRF-checked client production uses.
"""

import asyncio
import json
from datetime import date

import httpx
import pytest

from jobs_agent.careers import net
from jobs_agent.careers.adapters import (
    ADAPTERS,
    MAX_DESCRIPTION_CHARS,
    MAX_DETAIL_FETCHES,
    RAW_JOB_KEYS,
    ashby,
    generic,
    get_adapter,
    greenhouse,
    iso_date,
    lever,
    looks_uk,
    relevant,
    smartrecruiters,
    workday,
)
from jobs_agent.careers.detect import CareersSite

TERMS = ["accountant", "audit"]


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Public DNS for every host, and no real one-second pauses."""
    async def no_sleep(_seconds):
        return None
    monkeypatch.setattr(net, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(net, "_sleep", no_sleep)


def run(adapter, site, handler, terms=TERMS, max_jobs=60):
    """Call ``adapter`` against ``handler``; return (jobs, requests seen)."""
    seen: list[httpx.Request] = []

    def record(request):
        seen.append(request)
        return handler(request)

    async def go():
        async with net.make_client(transport=httpx.MockTransport(record)) as client:
            return await adapter(client, site, search_terms=terms, max_jobs=max_jobs)
    return asyncio.run(go()), seen


def assert_raw_jobs(jobs):
    for job in jobs:
        assert tuple(job) == RAW_JOB_KEYS
        assert job["employer"] is None


# --- shared helpers -----------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Graduate Auditor 2027", True),
    ("Trainee Accountant", True),
    ("Senior AUDIT Manager", True),        # matches a search term
    ("Summer Internship - Tax", True),
    ("Early Careers Programme", True),
    ("Entry Level Analyst", True),
    ("Staff Software Engineer", False),
    ("Head of Sales", False),
    # Whole words only: "intern" is not "International" or "Internal".
    ("International Tax Manager", False),
    ("Internal Communications Lead", False),
    ("Assistant Accountant", True),
    ("Finance Assistant", True),
    ("Entry-Level Analyst", True),
    ("Graduate Apprenticeship in Law", True),
    # "Assistant" in these is a senior rank, not an entry-level role.
    ("Assistant Vice President, Credit Risk", False),
    ("Assistant Director - Strategy", False),
    ("Assistant Manager, Treasury", False),
])
def test_relevant(title, expected):
    assert relevant(title, TERMS) is expected


def test_relevant_matches_search_terms_as_whole_words():
    assert relevant("Tax Associate", ["tax"])
    assert not relevant("Taxonomy Specialist", ["tax"])
    assert relevant("Data Analysts Wanted", ["data analyst"])  # plurals still count


def test_relevant_ignores_blank_terms():
    assert not relevant("Head of Sales", ["", "  "])


@pytest.mark.parametrize("value,expected", [
    ("2026-09-25T05:17:09-04:00", "2026-09-25"),
    ("2026-09-30T14:48:55.661Z", "2026-09-30"),
    ("2026-10-05", "2026-10-05"),
    (1786469891368, "2026-08-11"),
    (None, None),
    ("not a date", None),
])
def test_iso_date(value, expected):
    assert iso_date(value) == expected


def test_looks_uk_matches_whole_words():
    assert looks_uk("Birmingham, West Midlands, United Kingdom")
    assert looks_uk("London, UK")
    assert not looks_uk("Kyiv, Ukraine")
    assert not looks_uk("Singapore")


def test_registry_covers_every_ats_and_falls_back_to_generic():
    for ats in ("greenhouse", "lever", "lever-eu", "ashby", "smartrecruiters",
                "workday", "generic"):
        assert ats in ADAPTERS
    assert ADAPTERS["lever-eu"] is lever.fetch_jobs
    assert get_adapter("workday") is workday.fetch_jobs
    assert get_adapter("taleo") is generic.fetch_jobs


# --- Greenhouse -----------------------------------------------------------------

GREENHOUSE = {"jobs": [
    {"absolute_url": "https://job-boards.greenhouse.io/monzo/jobs/8232726",
     "location": {"name": "London"}, "id": 8232726,
     "updated_at": "2026-09-30T05:17:09-04:00", "first_published": "2026-09-28T09:04:42-04:00",
     "title": "Graduate Finance Analyst", "company_name": "Monzo",
     "content": "&lt;p&gt;&lt;strong&gt;About&lt;/strong&gt; the role&lt;/p&gt;"
                "&lt;ul&gt;&lt;li&gt;Month-end close &amp;amp; reporting&lt;/li&gt;&lt;/ul&gt;"},
    {"absolute_url": "https://job-boards.greenhouse.io/monzo/jobs/1",
     "location": {"name": "Cardiff, London or Remote (UK)"},
     "updated_at": "2026-09-25T05:17:09-04:00", "title": "Staff Backend Engineer",
     "content": "&lt;p&gt;x&lt;/p&gt;"},
]}


def test_greenhouse_maps_and_filters():
    def handler(request):
        assert request.url.host == "boards-api.greenhouse.io"
        assert request.url.path == "/v1/boards/monzo/jobs"
        assert request.url.params["content"] == "true"
        return httpx.Response(200, json=GREENHOUSE)

    jobs, _ = run(greenhouse.fetch_jobs, CareersSite("greenhouse", "monzo", ""), handler)
    assert_raw_jobs(jobs)
    assert [j["title"] for j in jobs] == ["Graduate Finance Analyst"]
    job = jobs[0]
    assert job["url"] == "https://job-boards.greenhouse.io/monzo/jobs/8232726"
    assert job["location"] == "London"
    assert job["posted"] == "2026-09-28"  # first_published wins over updated_at
    assert "<" not in job["description"] and "&amp;" not in job["description"]
    assert "Month-end close & reporting" in job["description"]


def test_greenhouse_caps_results_and_description_length():
    many = {"jobs": [{"title": f"Graduate {i}", "absolute_url": f"https://x/{i}",
                      "location": {"name": "London"}, "content": "a" * 20000}
                     for i in range(10)]}
    jobs, _ = run(greenhouse.fetch_jobs, CareersSite("greenhouse", "monzo", ""),
                  lambda r: httpx.Response(200, json=many), max_jobs=4)
    assert len(jobs) == 4
    assert len(jobs[0]["description"]) == MAX_DESCRIPTION_CHARS


def test_greenhouse_puts_uk_roles_first_before_capping():
    board = {"jobs": [{"title": f"Graduate {i}", "absolute_url": f"https://x/{i}",
                       "location": {"name": "New York"}} for i in range(5)]
             + [{"title": "Graduate UK", "absolute_url": "https://x/uk",
                 "location": {"name": "London, UK"}}]}
    jobs, _ = run(greenhouse.fetch_jobs, CareersSite("greenhouse", "monzo", ""),
                  lambda r: httpx.Response(200, json=board), max_jobs=2)
    assert [j["title"] for j in jobs] == ["Graduate UK", "Graduate 0"]


def test_greenhouse_quotes_the_slug_into_the_api_path():
    def handler(request):
        assert request.url.raw_path == b"/v1/boards/a%2Fb/jobs?content=true"
        return httpx.Response(200, json={"jobs": []})
    run(greenhouse.fetch_jobs, CareersSite("greenhouse", "a/b", ""), handler)


# --- Lever ----------------------------------------------------------------------

LEVER = [
    {"id": "774cf5c9", "text": "Audit Graduate Programme",
     "categories": {"commitment": "Full-time", "location": "London, United Kingdom",
                    "team": "Finance", "allLocations": ["London, United Kingdom"]},
     "createdAt": 1786469891368,
     "descriptionPlain": "A World-Changing Company\nWe build software.",
     "lists": [{"text": "What you'll do", "content": "<li>Support audits.</li><li>Learn.</li>"}],
     "additionalPlain": "We sponsor visas.",
     "hostedUrl": "https://jobs.lever.co/palantir/774cf5c9",
     "salaryRange": {"currency": "GBP", "interval": "per-year-salary",
                     "min": 32000, "max": 36000}},
    {"id": "2", "text": "Deployment Strategist, Internship",
     "categories": {"commitment": "Internship", "allLocations": ["Paris, France", "Berlin"]},
     "createdAt": 1786469891368, "descriptionPlain": "Paris.",
     "hostedUrl": "https://jobs.lever.co/palantir/2",
     "salaryRange": {"currency": "USD", "interval": "per-year-salary", "min": 1, "max": 2}},
    {"id": "3", "text": "Administrative Business Partner",
     "categories": {}, "hostedUrl": "https://jobs.lever.co/palantir/3"},
]


def test_lever_maps_and_filters():
    def handler(request):
        assert str(request.url) == "https://api.lever.co/v0/postings/palantir?mode=json"
        return httpx.Response(200, json=LEVER)

    jobs, _ = run(lever.fetch_jobs, CareersSite("lever", "palantir", ""), handler)
    assert_raw_jobs(jobs)
    assert [j["title"] for j in jobs] == ["Audit Graduate Programme",
                                          "Deployment Strategist, Internship"]
    first, second = jobs
    assert first["url"] == "https://jobs.lever.co/palantir/774cf5c9"
    assert first["location"] == "London, United Kingdom"
    assert first["employment_type"] == "Full-time"
    assert first["posted"] == "2026-08-11"
    assert (first["salary_min"], first["salary_max"]) == (32000.0, 36000.0)
    assert first["description"].index("A World-Changing") < first["description"].index(
        "What you'll do") < first["description"].index("Support audits.") < first[
        "description"].index("We sponsor visas.")
    assert second["location"] == "Paris, France, Berlin"
    assert (second["salary_min"], second["salary_max"]) == (None, None)  # USD ignored


def test_lever_eu_uses_the_eu_api():
    def handler(request):
        assert request.url.host == "api.eu.lever.co"
        return httpx.Response(200, json=[])

    jobs, seen = run(lever.fetch_jobs, CareersSite("lever-eu", "acme", ""), handler)
    assert jobs == [] and len(seen) == 1


@pytest.mark.parametrize("ats,first,second", [
    ("lever", "api.lever.co", "api.eu.lever.co"),
    ("lever-eu", "api.eu.lever.co", "api.lever.co"),
])
def test_lever_tries_the_other_instance_on_a_404(ats, first, second):
    # An employer lives on one instance only (Quantinuum: EU), whichever
    # host the user's link happened to name.
    def handler(request):
        if request.url.host == first:
            return httpx.Response(404, json={"ok": False})
        assert request.url.path == "/v0/postings/quantinuum"
        return httpx.Response(200, json=LEVER)

    jobs, seen = run(lever.fetch_jobs, CareersSite(ats, "quantinuum", ""), handler)
    assert [r.url.host for r in seen] == [first, second]
    assert jobs[0]["title"] == "Audit Graduate Programme"


def test_lever_other_errors_and_double_404s_still_raise():
    with pytest.raises(httpx.HTTPStatusError):
        run(lever.fetch_jobs, CareersSite("lever", "nobody", ""),
            lambda r: httpx.Response(404))
    seen: list = []
    with pytest.raises(httpx.HTTPStatusError):
        run(lever.fetch_jobs, CareersSite("lever", "acme", ""),
            lambda r: seen.append(r) or httpx.Response(500))
    assert len(seen) == 1  # only a 404 means "wrong instance"


def test_lever_puts_uk_roles_first_before_capping():
    postings = [{"text": f"Graduate {i}", "hostedUrl": f"https://x/{i}",
                 "categories": {"location": "San Francisco"}} for i in range(5)]
    postings.append({"text": "Graduate UK", "hostedUrl": "https://x/uk",
                     "categories": {"allLocations": ["Paris", "London"]}})
    jobs, _ = run(lever.fetch_jobs, CareersSite("lever", "palantir", ""),
                  lambda r: httpx.Response(200, json=postings), max_jobs=1)
    assert [j["title"] for j in jobs] == ["Graduate UK"]


# --- Ashby ----------------------------------------------------------------------

ASHBY = {"apiVersion": "1", "jobs": [
    {"id": "1", "title": "Graduate Financial Accountant", "employmentType": "FullTime",
     "location": "London", "secondaryLocations": [{"location": "Manchester"}],
     "publishedAt": "2026-09-14T10:20:16.160+00:00", "isListed": True,
     "jobUrl": "https://jobs.ashbyhq.com/multiverse/1",
     "descriptionHtml": "<p>Hi</p>", "descriptionPlain": "Multiverse is the upskilling platform.",
     "compensation": {"summaryComponents": [
         {"compensationType": "EquityPercentage", "interval": "NONE", "currencyCode": None,
          "minValue": None, "maxValue": None},
         {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "GBP",
          "minValue": 30000, "maxValue": 35000}]}},
    {"id": "2", "title": "Audit Intern", "employmentType": "Intern", "location": "London",
     "isListed": True, "jobUrl": "https://jobs.ashbyhq.com/multiverse/2",
     "descriptionHtml": "<p>Paid &amp; hybrid</p>", "publishedAt": "2026-09-01T00:00:00Z",
     "compensation": {"summaryComponents": [
         {"compensationType": "Salary", "interval": "1 YEAR", "currencyCode": "USD",
          "minValue": 211400, "maxValue": 290600}]}},
    {"id": "3", "title": "Graduate (hidden)", "isListed": False, "location": "London",
     "jobUrl": "https://jobs.ashbyhq.com/multiverse/3"},
    {"id": "4", "title": "Enterprise Account Executive, UK", "isListed": True,
     "location": "London", "jobUrl": "https://jobs.ashbyhq.com/multiverse/4"},
]}


def test_ashby_maps_filters_and_skips_unlisted():
    def handler(request):
        assert request.url.path == "/posting-api/job-board/multiverse"
        assert request.url.params["includeCompensation"] == "true"
        return httpx.Response(200, json=ASHBY)

    jobs, _ = run(ashby.fetch_jobs, CareersSite("ashby", "multiverse", ""), handler)
    assert_raw_jobs(jobs)
    assert [j["title"] for j in jobs] == ["Graduate Financial Accountant", "Audit Intern"]
    grad, intern = jobs
    assert grad["location"] == "London; Manchester"
    assert grad["employment_type"] == "Full-time"
    assert grad["posted"] == "2026-09-14"
    assert (grad["salary_min"], grad["salary_max"]) == (30000.0, 35000.0)
    assert grad["description"] == "Multiverse is the upskilling platform."
    assert intern["description"] == "Paid & hybrid"  # falls back to the HTML
    assert intern["employment_type"] == "Internship"
    assert intern["salary_min"] is None  # USD not reported as pounds


def test_ashby_puts_uk_roles_first_before_capping():
    board = {"jobs": [{"title": f"Graduate {i}", "jobUrl": f"https://x/{i}", "location": "Austin"}
                      for i in range(5)]
             + [{"title": "Graduate UK", "jobUrl": "https://x/uk", "location": "Remote",
                 "secondaryLocations": [{"location": "Manchester"}]}]}
    jobs, _ = run(ashby.fetch_jobs, CareersSite("ashby", "ramp", ""),
                  lambda r: httpx.Response(200, json=board), max_jobs=1)
    assert [j["title"] for j in jobs] == ["Graduate UK"]


# --- SmartRecruiters ------------------------------------------------------------

SR_API = "https://api.smartrecruiters.com/v1/companies/BoschGroup/postings"


def sr_posting(pid, name, city="Birmingham", country="gb", full=None):
    return {"id": pid, "name": name, "releasedDate": "2026-09-30T14:48:55.661Z",
            "location": {"city": city, "country": country, "fullLocation": full},
            "typeOfEmployment": {"id": "permanent", "label": "Full-time"},
            "ref": f"{SR_API}/{pid}"}


def sr_detail(pid):
    return {"id": pid, "postingUrl": f"https://jobs.smartrecruiters.com/BoschGroup/{pid}-slug",
            "jobAd": {"sections": {
                "companyDescription": {"title": "Company Description",
                                       "text": "<p>Bosch &amp; friends</p>"},
                "jobDescription": {"title": "Job Description", "text": "<ul><li>Plan</li></ul>"},
                "qualifications": {"title": "Qualifications", "text": ""},
            }}}


def test_smartrecruiters_searches_dedupes_and_maps():
    hits = {
        "graduate": [sr_posting("1", "Graduate Engineer",
                                full="Birmingham, West Midlands, United Kingdom"),
                     sr_posting("2", "Material Planning Intern", "Lincolnshire", "us")],
        "trainee": [sr_posting("1", "Graduate Engineer"),
                    sr_posting("3", "Senior Director")],  # matched on body text only
    }

    def handler(request):
        if request.url.path.endswith("/postings"):
            assert request.url.params["limit"] == "100"
            return httpx.Response(200, json={"content": hits.get(request.url.params["q"], [])})
        return httpx.Response(200, json=sr_detail(request.url.path.rsplit("/", 1)[1]))

    jobs, seen = run(smartrecruiters.fetch_jobs,
                     CareersSite("smartrecruiters", "BoschGroup", ""), handler)
    queries = [r.url.params["q"] for r in seen if r.url.path.endswith("/postings")]
    assert queries == ["graduate", "trainee", "accountant", "audit"]
    assert_raw_jobs(jobs)
    assert [j["title"] for j in jobs] == ["Graduate Engineer", "Material Planning Intern"]
    job = jobs[0]
    assert job["url"] == "https://jobs.smartrecruiters.com/BoschGroup/1-slug"
    assert job["location"] == "Birmingham, West Midlands, United Kingdom"
    assert job["posted"] == "2026-09-30"
    assert job["employment_type"] == "Full-time"
    assert job["description"] == ("Company Description\nBosch & friends\n\n"
                                  "Job Description\nPlan")
    assert jobs[1]["location"] == "Lincolnshire, US"


def test_smartrecruiters_caps_detail_fetches_and_puts_uk_first():
    postings = [sr_posting(str(i), f"Graduate {i}", "Austin", "us") for i in range(20)]
    postings.append(sr_posting("uk", "Graduate UK", "London", "gb",
                               full="London, England, United Kingdom"))

    def handler(request):
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"content": postings})
        return httpx.Response(200, json=sr_detail(request.url.path.rsplit("/", 1)[1]))

    jobs, seen = run(smartrecruiters.fetch_jobs,
                     CareersSite("smartrecruiters", "BoschGroup", ""), handler, terms=[])
    details = [r for r in seen if not r.url.path.endswith("/postings")]
    assert len(details) == MAX_DETAIL_FETCHES
    assert jobs[0]["title"] == "Graduate UK" and jobs[0]["description"]
    assert len(jobs) == 21
    assert not jobs[-1]["description"]  # beyond the cap: listed, undescribed
    assert jobs[-1]["url"] == "https://jobs.smartrecruiters.com/BoschGroup/19"


def test_smartrecruiters_survives_a_failed_detail():
    def handler(request):
        if request.url.path.endswith("/postings"):
            return httpx.Response(200, json={"content": [sr_posting("1", "Graduate")]})
        return httpx.Response(500)

    jobs, _ = run(smartrecruiters.fetch_jobs,
                  CareersSite("smartrecruiters", "BoschGroup", ""), handler)
    assert [j["title"] for j in jobs] == ["Graduate"] and jobs[0]["description"] == ""


def test_smartrecruiters_never_follows_a_foreign_ref():
    bad = {**sr_posting("1", "Graduate"), "ref": "https://evil.example/steal"}

    def handler(request):
        assert request.url.host == "api.smartrecruiters.com"
        return httpx.Response(200, json={"content": [bad]})

    jobs, _ = run(smartrecruiters.fetch_jobs,
                  CareersSite("smartrecruiters", "BoschGroup", ""), handler)
    assert len(jobs) == 1


# --- Workday --------------------------------------------------------------------

WD_HOST = "https://pwc.wd3.myworkdayjobs.com"
WD_API = "/wday/cxs/pwc/Global_Campus_Careers"
WD_SITE = CareersSite("workday", "pwc|wd3|Global_Campus_Careers",
                      f"{WD_HOST}/en-GB/Global_Campus_Careers")


def wd_posting(slug, title, where="London", posted="Posted 3 Days Ago"):
    return {"title": title, "externalPath": f"/job/{where}/{slug}", "timeType": "Full time",
            "locationsText": where, "postedOn": posted, "bulletFields": [slug]}


def wd_detail(path):
    return {"jobPostingInfo": {
        "title": "t", "jobDescription": "<p><b>Line of Service</b></p>Assurance<p>Audit &amp; more</p>",
        "location": "London", "additionalLocations": ["Leeds"], "startDate": "2026-10-01",
        "timeType": "Full time", "externalUrl": f"{WD_HOST}/Global_Campus_Careers{path}"}}


def test_workday_searches_dedupes_and_maps():
    hits = {
        "graduate": [wd_posting("A_1", "Graduate Audit Associate 2027"),
                     wd_posting("B_2", "Tax Trainee", "Singapore", "Posted Today")],
        "trainee": [wd_posting("B_2", "Tax Trainee", "Singapore", "Posted Today"),
                    wd_posting("C_3", "Senior Manager, Deals")],
        "accountant": [wd_posting("A_1", "Graduate Audit Associate 2027")],
    }

    def handler(request):
        if request.method == "POST":
            assert request.url.path == f"{WD_API}/jobs"
            body = json.loads(request.content)
            assert body == {"appliedFacets": {}, "limit": 20, "offset": 0,
                            "searchText": body["searchText"]}
            return httpx.Response(200, json={"total": 3,
                                             "jobPostings": hits.get(body["searchText"], [])})
        assert request.url.path.startswith(f"{WD_API}/job/")
        return httpx.Response(200, json=wd_detail(request.url.path[len(WD_API):]))

    jobs, seen = run(workday.fetch_jobs, WD_SITE, handler)
    searches = [json.loads(r.content)["searchText"] for r in seen if r.method == "POST"]
    assert searches == ["graduate", "trainee", "accountant", "audit"]
    details = [r for r in seen if r.method == "GET"]
    assert len(details) == 2  # A_1 and B_2 once each; C_3 isn't relevant
    assert_raw_jobs(jobs)
    assert [j["title"] for j in jobs] == ["Graduate Audit Associate 2027", "Tax Trainee"]
    job = jobs[0]
    assert job["url"] == f"{WD_HOST}/Global_Campus_Careers/job/London/A_1"
    assert job["location"] == "London; Leeds"
    assert job["posted"] == "2026-10-01"
    assert job["employment_type"] == "Full time"
    assert job["description"].startswith("Line of Service")
    assert "Audit & more" in job["description"] and "<" not in job["description"]


def test_workday_caps_detail_fetches_and_builds_urls_without_details():
    postings = [wd_posting(f"J_{i}", f"Graduate {i}", "Mumbai", "Posted 30+ Days Ago")
                for i in range(18)]
    postings.append(wd_posting("UK_1", "Graduate UK", "London, United Kingdom"))

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"jobPostings": postings})
        return httpx.Response(200, json=wd_detail(request.url.path[len(WD_API):]))

    jobs, seen = run(workday.fetch_jobs, WD_SITE, handler, terms=[])
    assert sum(r.method == "GET" for r in seen) == MAX_DETAIL_FETCHES
    assert jobs[0]["title"] == "Graduate UK"
    last = jobs[-1]
    assert last["description"] == "" and last["posted"] is None
    assert last["url"] == f"{WD_HOST}/Global_Campus_Careers/job/Mumbai/J_17"
    assert last["location"] == "Mumbai"


def test_workday_reads_a_second_page_when_the_first_is_full():
    first = [wd_posting(f"P1_{i}", f"Graduate {i}") for i in range(workday.PAGE)]
    second = [wd_posting("P2_0", "Graduate Late"), wd_posting("P1_0", "Graduate 0")]

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            if body["searchText"] != "graduate":
                return httpx.Response(200, json={"jobPostings": []})
            page = first if body["offset"] == 0 else second
            return httpx.Response(200, json={"jobPostings": page})
        return httpx.Response(200, json={})

    jobs, seen = run(workday.fetch_jobs, WD_SITE, handler, terms=[])
    pages = [(json.loads(r.content)["searchText"], json.loads(r.content)["offset"])
             for r in seen if r.method == "POST"]
    # Only the full first page earns a second request; short pages don't.
    assert pages == [("graduate", 0), ("graduate", workday.PAGE), ("trainee", 0)]
    titles = [j["title"] for j in jobs]
    assert "Graduate Late" in titles and len(titles) == workday.PAGE + 1  # deduped


def test_workday_requests_are_bounded():
    full = [wd_posting(f"J_{i}", f"Graduate {i}") for i in range(workday.PAGE)]

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"jobPostings": full})
        return httpx.Response(200, json={})

    _, seen = run(workday.fetch_jobs, WD_SITE, handler,
                  terms=["a", "b", "c", "d", "e"])
    assert len(seen) <= workday.MAX_REQUESTS


def test_workday_a_bare_location_count_without_details_becomes_unknown():
    postings = [wd_posting(f"J_{i}", f"Graduate {i}", "Mumbai") for i in range(MAX_DETAIL_FETCHES)]
    postings += [{**wd_posting("M_1", "Graduate Multi"), "locationsText": "2 Locations"},
                 {**wd_posting("M_2", "Graduate One"), "locationsText": "1 Location"}]

    def handler(request):
        if request.method == "POST":
            return httpx.Response(200, json={"jobPostings": postings})
        return httpx.Response(200, json=wd_detail(request.url.path[len(WD_API):]))

    jobs, _ = run(workday.fetch_jobs, WD_SITE, handler, terms=[])
    by_title = {j["title"]: j for j in jobs}
    assert by_title["Graduate Multi"]["location"] == ""
    assert by_title["Graduate One"]["location"] == ""
    assert by_title["Graduate 0"]["location"] == "London; Leeds"  # detail wins


def test_workday_quotes_the_site_into_the_api_path():
    site = CareersSite("workday", "pwc|wd3|a b", "")

    def handler(request):
        assert request.url.raw_path == b"/wday/cxs/pwc/a%20b/jobs"
        return httpx.Response(200, json={"jobPostings": []})
    run(workday.fetch_jobs, site, handler, terms=[])


def test_workday_refuses_a_tenant_that_is_not_a_hostname_label():
    with pytest.raises(ValueError):
        run(workday.fetch_jobs, CareersSite("workday", "evil.example/x|wd3|s", ""),
            lambda r: httpx.Response(200, json={}), terms=[])


def test_smartrecruiters_quotes_the_slug_into_the_api_path():
    def handler(request):
        assert request.url.raw_path.startswith(b"/v1/companies/a%2Fb/postings")
        return httpx.Response(200, json={"content": []})
    run(smartrecruiters.fetch_jobs, CareersSite("smartrecruiters", "a/b", ""), handler)


@pytest.mark.parametrize("text,expected", [
    ("Posted Today", "2026-10-05"),
    ("Posted Yesterday", "2026-10-04"),
    ("Posted 3 Days Ago", "2026-10-02"),
    ("Posted 30+ Days Ago", None),
    ("", None),
])
def test_workday_relative_dates(text, expected):
    assert workday.relative_date(text, today=date(2026, 10, 5)) == expected
