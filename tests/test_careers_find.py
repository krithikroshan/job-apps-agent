"""Finding a company's careers site from its name and homepage. Every
request goes to a fake web (httpx.MockTransport); nothing leaves the test."""

import asyncio
import json

import httpx
import pytest

from jobs_agent.careers import find, net


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(net, "_resolve", lambda host: ["93.184.216.34"])

    async def no_wait(seconds):
        return None
    monkeypatch.setattr(net, "_sleep", no_wait)


def web(pages):
    """A fake web: {url: html or (status, body)}; anything else is a 404.
    Returns (client, requested urls)."""
    seen = []

    def handler(request):
        url = str(request.url)
        seen.append(url)
        page = pages.get(url)
        if page is None:
            return httpx.Response(404, text="not found")
        status, body = page if isinstance(page, tuple) else (200, page)
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body, headers={"Content-Type": "text/html"})
    return net.make_client(httpx.MockTransport(handler)), seen


def run(pages, name="Firm", website="firm.co.uk"):
    client, seen = web(pages)

    async def go():
        async with client:
            return await find.find_careers_site(client, name, website)
    return asyncio.run(go()), seen


def link(href, text):
    return f'<a href="{href}">{text}</a>'


def test_follows_the_careers_link_to_the_job_board_behind_it():
    found, _ = run({
        "https://firm.co.uk/": link("/about", "About") + link("/careers", "Careers"),
        "https://firm.co.uk/careers": link(
            "https://firm.wd3.myworkdayjobs.com/en-GB/Graduates", "Search jobs"),
    })
    assert found.site.ats == "workday"
    assert found.site.url == "https://firm.wd3.myworkdayjobs.com/Graduates"
    assert found.via == "job board"


def test_a_job_board_linked_from_the_homepage_is_used_straight_away():
    found, seen = run({
        "https://firm.co.uk/": link("https://boards.greenhouse.io/firm", "Jobs"),
    })
    assert found.site.ats == "greenhouse"
    assert seen == ["https://firm.co.uk/"]


def test_early_careers_beats_a_general_careers_link():
    found, _ = run({
        "https://firm.co.uk/": link("/careers", "Careers")
                               + link("/careers/early-careers", "Graduates & early careers"),
        "https://firm.co.uk/careers/early-careers": "<p>Our graduate programme</p>",
        "https://firm.co.uk/careers": "<p>Experienced hires</p>",
    })
    assert found.site.url == "https://firm.co.uk/careers/early-careers"
    assert found.via == "careers page"


def test_a_careers_site_on_its_own_domain_is_followed():
    found, _ = run({
        "https://firm.co.uk/": link("https://www.firmcareers.co.uk/", "Careers"),
        "https://www.firmcareers.co.uk/": link("https://jobs.lever.co/firm", "View vacancies"),
    })
    assert found.site.ats == "lever"


def test_social_and_job_aggregator_links_are_ignored():
    found, _ = run({
        "https://firm.co.uk/": link("https://www.linkedin.com/company/firm/jobs", "Careers")
                               + link("https://uk.indeed.com/cmp/firm", "Jobs"),
    })
    assert found is None


def test_without_a_careers_link_the_usual_paths_are_tried():
    found, _ = run({
        "https://firm.co.uk/": "<p>Welcome</p>",
        "https://firm.co.uk/careers": link("https://jobs.ashbyhq.com/firm", "Open roles"),
    })
    assert found.site.ats == "ashby"


def test_a_homepage_that_fails_still_gets_the_usual_paths():
    found, _ = run({
        "https://firm.co.uk/": (500, "oops"),
        "https://firm.co.uk/jobs": "<h1>Jobs at Firm</h1>",
    })
    assert found.site.url == "https://firm.co.uk/jobs"


def test_when_the_careers_links_are_dead_the_usual_paths_are_tried():
    found, _ = run({
        "https://firm.co.uk/": link("/join-us", "Join us"),
        "https://firm.co.uk/jobs": link("https://jobs.lever.co/firm", "Vacancies"),
    })
    assert found.site.ats == "lever"


def test_the_job_search_behind_a_careers_page_is_followed_to_its_board():
    found, _ = run({
        "https://firm.co.uk/": link("/careers/early-careers/", "Early careers"),
        "https://firm.co.uk/careers/early-careers/": link("/about", "About us")
            + link("/careers/early-careers-job-search/", "Apply now"),
        "https://firm.co.uk/careers/early-careers-job-search/": link(
            "https://firm.wd3.myworkdayjobs.com/Trainees", "Search roles"),
    })
    assert found.site.ats == "workday"


def test_a_vacancies_list_beats_the_marketing_page_that_links_to_it():
    found, _ = run({
        "https://firm.co.uk/": link("/en/careers", "Careers"),
        "https://firm.co.uk/en/careers": link("https://apply.firm.co.uk/home/jobs",
                                              "View current vacancies"),
        "https://apply.firm.co.uk/home/jobs": link("/home/jobs/101", "Paralegal")
                                              + link("/home/jobs/102", "Trainee paralegal"),
    })
    assert found.site.url == "https://apply.firm.co.uk/home/jobs"


def test_news_stories_that_mention_students_are_not_careers_pages():
    found, _ = run({
        "https://firm.co.uk/": link("/news/2026/april/studentcrowd-funding", "Read the story")
                               + link("/news/student-awards", "Student awards"),
        "https://firm.co.uk/news/2026/april/studentcrowd-funding": "<p>Story</p>",
        "https://firm.co.uk/news/student-awards": "<p>Awards</p>",
    })
    assert found is None


def test_tracking_parameters_are_dropped_from_the_link():
    found, _ = run({
        "https://firm.co.uk/": link(
            "https://careers.firm.co.uk/early/?source=Corp&utm_source=Corp&utm_medium=x&page=2",
            "Early careers"),
        "https://careers.firm.co.uk/early/?page=2": "<p>Graduate roles</p>",
    })
    assert found.site.url == "https://careers.firm.co.uk/early/?page=2"


@pytest.mark.parametrize("linked,board", [
    ("https://ukgrantt.wd3.myworkdayjobs.com/TraineeCareers/job/Birmingham/Tax_TRN27/apply",
     "https://ukgrantt.wd3.myworkdayjobs.com/TraineeCareers"),
    ("https://job-boards.eu.greenhouse.io/wise/jobs/123?gh_src=abc",
     "https://job-boards.eu.greenhouse.io/wise"),
    ("https://jobs.lever.co/firm/5f1c-uuid/apply", "https://jobs.lever.co/firm"),
    ("https://firm.pinpointhq.com/en/postings/abc", "https://firm.pinpointhq.com/"),
])
def test_a_link_to_one_job_is_taken_back_to_its_board(linked, board):
    found, _ = run({"https://firm.co.uk/": link(linked, "Apply")})
    assert found.site.url == board


def test_a_page_past_the_careers_page_counts_only_if_it_lists_jobs():
    found, _ = run({
        "https://firm.co.uk/": link("/careers", "Careers"),
        "https://firm.co.uk/careers": link("/careers/ai-usage-in-applications", "How we apply AI"),
        "https://firm.co.uk/careers/ai-usage-in-applications": "<p>Our policy</p>",
    })
    assert found.site.url == "https://firm.co.uk/careers"


def test_a_careers_page_that_lists_jobs_beats_a_page_past_it():
    jobs = link("/job/london/audit-graduate/1", "Audit Graduate") + link(
        "/job/leeds/tax-graduate/2", "Tax Graduate")
    found, _ = run({
        "https://firm.co.uk/": link("https://careers.firm.co.uk/early-careers/", "Apply now"),
        "https://careers.firm.co.uk/early-careers/": link("/ai-usage", "AI & applying") + jobs,
        "https://careers.firm.co.uk/ai-usage": jobs,
    })
    assert found.site.url == "https://careers.firm.co.uk/early-careers/"


def test_a_bare_domain_that_fails_is_retried_with_www():
    found, _ = run({
        "https://bankofengland.co.uk/": (500, "bad certificate"),
        "https://www.bankofengland.co.uk/": link("/careers", "Careers"),
        "https://www.bankofengland.co.uk/careers": link(
            "https://boe.wd3.myworkdayjobs.com/BoE", "Search vacancies"),
    }, name="Bank of England", website="bankofengland.co.uk")
    assert found.site.ats == "workday"


@pytest.mark.parametrize("website", ["gov.uk", "www.gov.uk", "nhs.uk", "ac.uk", "co.uk"])
def test_a_domain_shared_by_many_organisations_is_not_crawled(website):
    found, seen = run({
        "https://gov.uk/": link("/careers", "Careers"),
        "https://www.gov.uk/": link("/careers", "Careers"),
        "https://www.gov.uk/careers": "<p>Civil service careers</p>",
    }, name="Government Legal Department", website=website)
    assert found is None
    assert not any("gov.uk" in url for url in seen)


def test_a_greenhouse_board_with_the_same_name_is_found_without_a_website():
    found, _ = run({
        "https://boards-api.greenhouse.io/v1/boards/monzo": {"name": "Monzo"},
    }, name="Monzo", website="")
    assert found.site.ats == "greenhouse"
    assert found.site.url == "https://boards.greenhouse.io/monzo"


def test_a_greenhouse_board_for_a_different_firm_is_not_taken():
    found, _ = run({
        "https://boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme Robotics Inc"},
    }, name="Acme", website="")
    assert found is None


def test_nothing_found_is_none():
    found, _ = run({"https://firm.co.uk/": "<p>Hello</p>"})
    assert found is None


def test_a_private_website_is_never_fetched(monkeypatch):
    monkeypatch.setattr(net, "_resolve", lambda host: ["10.0.0.7"])
    found, seen = run({"https://firm.co.uk/": link("/careers", "Careers")})
    assert found is None
    assert not any("firm.co.uk" in url for url in seen)


def test_the_number_of_pages_read_is_bounded():
    pages = {"https://firm.co.uk/": "".join(
        link(f"/careers/{i}", f"Careers {i}") for i in range(30))}
    found, seen = run(pages)
    assert len(seen) <= find.MAX_FETCHES


@pytest.mark.parametrize("name,slugs", [
    ("Monzo", ["monzo"]),
    ("Grant Thornton UK", ["grantthorntonuk", "grant-thornton-uk", "grantthornton"]),
    ("Kennedys Law LLP", ["kennedyslawllp", "kennedys-law-llp", "kennedyslaw"]),
])
def test_slug_guesses(name, slugs):
    assert find.slug_guesses(name)[:len(slugs)] == slugs
