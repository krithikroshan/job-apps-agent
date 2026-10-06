"""Recognising which job-board software a careers URL runs on."""

import pytest

from jobs_agent.careers.detect import detect


@pytest.mark.parametrize("url,ats,slug", [
    ("https://boards.greenhouse.io/monzo", "greenhouse", "monzo"),
    ("https://job-boards.greenhouse.io/monzo/jobs/123", "greenhouse", "monzo"),
    ("https://job-boards.eu.greenhouse.io/wise", "greenhouse", "wise"),
    ("https://jobs.lever.co/palantir", "lever", "palantir"),
    ("https://jobs.eu.lever.co/some-firm/abc-123", "lever-eu", "some-firm"),
    ("https://jobs.ashbyhq.com/ramp", "ashby", "ramp"),
    ("https://careers.smartrecruiters.com/Visa1", "smartrecruiters", "Visa1"),
    ("https://jobs.smartrecruiters.com/Visa1/7438", "smartrecruiters", "Visa1"),
    ("https://pwc.wd3.myworkdayjobs.com/en-GB/Global_Experienced_Careers", "workday",
     "pwc|wd3|Global_Experienced_Careers"),
    ("https://acme.wd103.myworkdayjobs.com/Careers/job/London/123", "workday", "acme|wd103|Careers"),
    ("https://www.example.co.uk/careers/graduates", "generic", ""),
    ("https://menzies.pinpointhq.com/", "pinpoint", "menzies"),
    ("https://MooreKS.pinpointhq.com/en/postings/abc", "pinpoint", "mooreks"),
    ("https://www.rsmuk.com/job-search-index.json", "json-index", "www.rsmuk.com"),
    ("https://careers.firm.example/jobs/Index.JSON?limit=500", "json-index",
     "careers.firm.example"),
    # A known ATS wins over the .json rule.
    ("https://boards.greenhouse.io/monzo/jobs.json", "greenhouse", "monzo"),
])
def test_detect(url, ats, slug):
    site = detect(url)
    assert (site.ats, site.slug) == (ats, slug)


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/embed/job_board?for=../../admin",
    "https://boards.greenhouse.io/embed/job_board?for=a%2Fb",
    "https://boards.greenhouse.io/embed/job_board?for=a?x=1",
    "https://boards.greenhouse.io/embed/job_board?for=-leading-dash",
    "https://jobs.lever.co/a..b",
    "https://jobs.ashbyhq.com/" + "a" * 101,
    "https://careers.smartrecruiters.com/Visa%3F1",
    "https://pwc.wd3.myworkdayjobs.com/en-GB/..",
    "https://pwc.wd3.myworkdayjobs.com/Site%2F..%2Fx",
    "https://a.b.pinpointhq.com/",
    "https://www.pinpointhq.com/",
    "https://-x.pinpointhq.com/",
])
def test_odd_slugs_fall_back_to_generic(url):
    # The slug is interpolated into API URLs, so anything that could change
    # the path or query is refused; the page is still read generically.
    site = detect(url)
    assert (site.ats, site.slug) == ("generic", "")
    assert site.url == url
