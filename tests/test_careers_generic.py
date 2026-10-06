"""The fallback adapter for careers sites with no dedicated ATS adapter:
JSON-LD first, then a few relevant-looking job pages."""

import asyncio

import httpx
import pytest

from jobs_agent.careers import net
from jobs_agent.careers.adapters import generic
from jobs_agent.careers.detect import CareersSite

SITE = CareersSite(ats="generic", slug="", url="https://acme.example/careers")

LISTING = """<html><body>
<a href="/careers/jobs/1">Audit Graduate Scheme 2027</a>
<a href="/careers/jobs/2">Senior Tax Manager</a>
<a href="/careers/jobs/3">Marketing Apprentice</a>
<a href="/careers/jobs/4">Summer Internship</a>
<a href="/careers/jobs/5">Junior Data Analyst</a>
<a href="/about">About us</a>
</body></html>"""

JOB_1 = """<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Audit Graduate Scheme 2027",
 "jobLocation": {"address": "Manchester"}, "description": "<p>Audit work</p>"}
</script></head><body>ignored</body></html>"""

JOB_3 = """<html><head><title>Apprentice</title></head><body>
<nav>Home | Careers</nav><h1>Marketing Apprentice</h1><p>Learn marketing &amp; more.</p>
<footer>Cookie policy</footer></body></html>"""


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(net, "_sleep", instant)


def client_for(pages: dict[str, httpx.Response], seen: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return pages.get(str(request.url), httpx.Response(404))
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def fetch(pages, terms=(), seen=None, **kwargs):
    async with client_for(pages, seen) as client:
        return await generic.fetch_jobs(client, SITE, search_terms=list(terms), **kwargs)


def test_json_ld_on_the_careers_page_is_used_directly():
    page = "".join(
        f'<script type="application/ld+json">{{"@type": "JobPosting", "title": "Job {i}"}}</script>'
        for i in range(5))
    jobs = run(fetch({SITE.url: httpx.Response(200, text=page)}, max_jobs=3))
    assert [j["title"] for j in jobs] == ["Job 0", "Job 1", "Job 2"]


def test_relevant_job_pages_are_followed_and_bad_ones_skipped():
    seen: list[str] = []
    pages = {
        SITE.url: httpx.Response(200, text=LISTING),
        "https://acme.example/careers/jobs/1": httpx.Response(200, text=JOB_1),
        "https://acme.example/careers/jobs/3": httpx.Response(200, text=JOB_3),
        "https://acme.example/careers/jobs/4": httpx.Response(500),
        "https://acme.example/careers/jobs/5": httpx.Response(200, text="<p>Analyse data</p>"),
    }
    jobs = run(fetch(pages, seen=seen))

    assert [j["title"] for j in jobs] == [
        "Audit Graduate Scheme 2027", "Marketing Apprentice", "Junior Data Analyst"]
    assert jobs[0]["location"] == "Manchester"
    assert jobs[0]["url"] == "https://acme.example/careers/jobs/1"
    apprentice = jobs[1]
    assert apprentice["description"].startswith("Marketing Apprentice")
    assert "Learn marketing & more." in apprentice["description"]
    assert "Cookie policy" not in apprentice["description"]
    assert set(apprentice) == set(jobs[0])
    # Not a graduate role and no matching search term, so never fetched.
    assert "https://acme.example/careers/jobs/2" not in seen


def test_search_terms_widen_the_links_followed():
    pages = {SITE.url: httpx.Response(200, text=LISTING),
             "https://acme.example/careers/jobs/2": httpx.Response(200, text="<p>Tax</p>")}
    jobs = run(fetch(pages, terms=["TAX"]))
    assert "Senior Tax Manager" in [j["title"] for j in jobs]


def test_link_text_is_matched_on_whole_words():
    listing = """<a href="/careers/jobs/1">Industry Insights</a>
                 <a href="/careers/jobs/2">International Tax Partner</a>
                 <a href="/careers/jobs/3">Entry-level Paralegal</a>"""
    seen = []
    run(fetch({SITE.url: httpx.Response(200, text=listing)}, seen=seen))
    assert seen == [SITE.url, "https://acme.example/careers/jobs/3"]


def test_robots_txt_is_ignored():
    pages = {
        "https://acme.example/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
        SITE.url: httpx.Response(200, text=LISTING),
        "https://acme.example/careers/jobs/1": httpx.Response(200, text=JOB_1),
        "https://acme.example/careers/jobs/3": httpx.Response(200, text=JOB_3),
    }
    seen: list[str] = []
    jobs = run(fetch(pages, seen=seen))
    assert len(jobs) == 2
    assert "https://acme.example/robots.txt" not in seen


def test_at_most_twelve_job_pages_are_fetched():
    links = "".join(f'<a href="/jobs/{i}">Graduate role {i}</a>' for i in range(30))
    seen: list[str] = []
    jobs = run(fetch({SITE.url: httpx.Response(200, text=links)}, seen=seen))
    assert jobs == []  # every job page 404s and is skipped
    assert len([u for u in seen if "/jobs/" in u]) == generic.MAX_DETAIL_PAGES


def test_a_failing_careers_page_raises():
    with pytest.raises(httpx.HTTPStatusError):
        run(fetch({SITE.url: httpx.Response(503)}))


# --- shapes from real UK sites (Deloitte, KPMG; October 2026) ---------------------

def test_the_link_path_counts_when_the_link_text_says_nothing():
    # Deloitte's early-careers listing names programmes ("Assurance"); the
    # "UKEarlyCareers" in the path is what marks them entry-level. KPMG's
    # links say "View role"; the slug carries the title.
    site = CareersSite("generic", "", "https://apply.deloitte.co.uk/UKEarlyCareers/SearchJobs/")
    listing = """<a href="/UKEarlyCareers/JobDetail/Assurance/20502">Assurance</a>
                 <a href="/Vacancies/GraduateTaxACAReading2027/3271">View role</a>
                 <a href="/Vacancies/SeniorTaxManager/9">View role</a>
                 <a href="/Vacancies/international-tax-lead/8">View role</a>"""
    seen: list[str] = []

    async def go():
        async with client_for({site.url: httpx.Response(200, text=listing)}, seen) as client:
            return await generic.fetch_jobs(client, site, search_terms=[])
    run(go())
    assert seen[1:] == ["https://apply.deloitte.co.uk/UKEarlyCareers/JobDetail/Assurance/20502",
                        "https://apply.deloitte.co.uk/Vacancies/GraduateTaxACAReading2027/3271"]


def test_a_call_to_action_link_takes_its_title_from_the_job_page():
    listing = '<a href="/careers/jobs/graduate-tax-reading">View role</a>'
    page = """<html><head><title>Vacancies</title></head><body>
              <h1>Graduate Tax - ACA <em>Reading</em> Autumn 2027</h1><p>Tax work.</p></body></html>"""
    pages = {SITE.url: httpx.Response(200, text=listing),
             "https://acme.example/careers/jobs/graduate-tax-reading": httpx.Response(200, text=page)}
    [job] = run(fetch(pages))
    assert job["title"] == "Graduate Tax - ACA Reading Autumn 2027"


def test_a_job_page_without_an_h1_keeps_the_link_text():
    listing = '<a href="/careers/jobs/1">Read more</a><a href="/careers/jobs/2">Graduate Auditor</a>'
    pages = {SITE.url: httpx.Response(200, text=listing),
             "https://acme.example/careers/jobs/2": httpx.Response(200, text="<p>Audit</p>")}
    [job] = run(fetch(pages))
    assert job["title"] == "Graduate Auditor"


def test_json_ld_without_a_description_gets_the_page_text():
    # Deloitte's job pages carry JSON-LD with just a title and a date.
    page = """<html><head><script type="application/ld+json">
              {"@type": "JobPosting", "title": "Assurance", "datePosted": "2026-08-11"}
              </script></head><body><nav>Menu</nav>
              <h1>Assurance</h1><p>Graduate programme with ACA study support.</p></body></html>"""
    pages = {SITE.url: httpx.Response(200, text=LISTING),
             "https://acme.example/careers/jobs/1": httpx.Response(200, text=page)}
    jobs = run(fetch(pages))
    assert jobs[0]["title"] == "Assurance"
    assert jobs[0]["posted"] == "2026-08-11"
    assert "ACA study support" in jobs[0]["description"]
    assert "Menu" not in jobs[0]["description"]
