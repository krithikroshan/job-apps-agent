"""Reading schema.org JobPosting data, which many careers pages publish so
search engines can list their jobs."""

from urllib.parse import urljoin

import pytest

from jobs_agent.careers.jsonld import job_links, job_postings

PAGE = """<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "JobPosting",
 "title": "Audit Graduate Programme 2027", "datePosted": "2026-09-30",
 "description": "<p>Join our <b>audit</b> team &amp; study for the ACA.</p>",
 "hiringOrganization": {"@type": "Organization", "name": "Acme LLP"},
 "jobLocation": {"@type": "Place", "address": {"addressLocality": "London",
   "addressCountry": "GB"}},
 "employmentType": "FULL_TIME", "url": "https://acme.example/jobs/1",
 "baseSalary": {"@type": "MonetaryAmount", "currency": "GBP",
   "value": {"@type": "QuantitativeValue", "minValue": 30000, "maxValue": 32000}}}
</script>
<script type="application/ld+json">
{"@graph": [{"@type": "JobPosting", "title": "Tax Trainee", "url": "/jobs/2",
  "jobLocation": [{"address": {"addressLocality": "Leeds"}}]},
  {"@type": "WebPage", "name": "ignore me"}]}
</script>
<script type="application/ld+json">not json at all</script>
</head><body></body></html>"""


def test_job_postings_are_read_from_json_ld():
    jobs = job_postings(PAGE, base_url="https://acme.example/careers")
    assert [j["title"] for j in jobs] == ["Audit Graduate Programme 2027", "Tax Trainee"]
    first = jobs[0]
    assert first["location"] == "London, GB"
    assert first["employer"] == "Acme LLP"
    assert first["salary_min"] == 30000 and first["salary_max"] == 32000
    assert first["posted"] == "2026-09-30"
    assert "audit team & study for the ACA" in first["description"]
    assert "<b>" not in first["description"]
    assert jobs[1]["url"] == "https://acme.example/jobs/2"
    assert jobs[1]["location"] == "Leeds"


def test_job_links_finds_likely_job_pages_on_the_same_site():
    html = """<a href="/careers/jobs/123-audit-graduate">Audit Graduate 2027</a>
              <a href="https://acme.example/jobs/tax-trainee">Tax Trainee</a>
              <a href="https://elsewhere.example/jobs/x">Other site job</a>
              <a href="/about-us">About us</a>
              <a href="mailto:jobs@acme.example">Email</a>"""
    links = job_links(html, base_url="https://acme.example/careers")
    assert links == [("Audit Graduate 2027", "https://acme.example/careers/jobs/123-audit-graduate"),
                     ("Tax Trainee", "https://acme.example/jobs/tax-trainee")]


def test_item_lists_string_addresses_and_plain_salaries():
    page = """<script type="application/ld+json">
    [{"@type": "ItemList", "itemListElement": [
       {"@type": "ListItem", "item": {"@type": "JobPosting", "title": "Trainee Solicitor",
        "jobLocation": {"address": "Bristol"}, "employmentType": ["FULL_TIME", "TEMPORARY"],
        "baseSalary": {"value": 28000}, "datePosted": "2026-10-01T09:00:00Z"}}]},
     {"@type": "JobPosting", "name": ""}]
    </script>"""
    [job] = job_postings(page, base_url="https://firm.example/careers")
    assert job["location"] == "Bristol"
    assert job["salary_min"] == job["salary_max"] == 28000
    assert job["employment_type"] == "FULL_TIME, TEMPORARY"
    assert job["posted"] == "2026-10-01"
    assert job["url"] == "https://firm.example/careers"
    assert job["employer"] is None
    assert set(job) == {"title", "url", "location", "employer", "description", "posted",
                        "salary_min", "salary_max", "employment_type"}


def test_job_links_are_deduplicated_and_skip_empty_text():
    html = """<a href="/jobs/1">Graduate</a><a href="/jobs/1#apply">Graduate again</a>
              <a href="/jobs/2"><img src="x.png"></a>"""
    assert job_links(html, "https://acme.example/") == [("Graduate", "https://acme.example/jobs/1")]


def test_html_to_text_handles_double_escaped_markup_and_keeps_breaks():
    from jobs_agent.careers.text import html_to_text
    assert html_to_text(None) == ""
    escaped = "&lt;p&gt;Hello &amp;amp; welcome&lt;/p&gt;&lt;ul&gt;&lt;li&gt;One&lt;/li&gt;&lt;li&gt;Two&lt;/li&gt;&lt;/ul&gt;"
    assert html_to_text(escaped) == "Hello & welcome\n\nOne\nTwo"
    assert html_to_text("<p>A   b<br>c</p><script>x()</script>") == "A b\nc"



def _salary_page(base_salary):
    import json
    posting = {"@type": "JobPosting", "title": "Graduate", "baseSalary": base_salary}
    return f'<script type="application/ld+json">{json.dumps(posting)}</script>'


@pytest.mark.parametrize("base_salary,expected", [
    # Annual GBP, stated or implied.
    ({"currency": "GBP", "value": {"minValue": 30000, "maxValue": 32000, "unitText": "YEAR"}},
     (30000, 32000)),
    ({"value": {"value": 28000}}, (28000, 28000)),
    ({"currency": "gbp", "value": 25000}, (25000, 25000)),
    # Monthly pay is annualised.
    ({"currency": "GBP", "value": {"minValue": 2000, "maxValue": 2500, "unitText": "MONTH"}},
     (24000, 30000)),
    # Pay we can't honestly compare with an annual salary is dropped.
    ({"currency": "GBP", "value": {"value": 12.5, "unitText": "HOUR"}}, (None, None)),
    ({"currency": "GBP", "value": {"value": 500, "unitText": "WEEK"}}, (None, None)),
    ({"currency": "GBP", "value": {"value": 120, "unitText": "DAY"}}, (None, None)),
    ({"currency": "GBP", "value": {"value": 12.5}}, (None, None)),  # no unit, too small
    ({"currency": "USD", "value": {"value": 90000, "unitText": "YEAR"}}, (None, None)),
    ({"currency": "EUR", "value": 40000}, (None, None)),
])
def test_salary_currency_and_unit(base_salary, expected):
    [job] = job_postings(_salary_page(base_salary), base_url="https://x.example/")
    assert (job["salary_min"], job["salary_max"]) == expected


# Link shapes copied from real UK graduate careers sites (October 2026).
REAL_LINKS = [
    ("https://apply.deloitte.co.uk/UKEarlyCareers/SearchJobs/",
     "/UKEarlyCareers/JobDetail/Assurance/20502", "Assurance"),
    ("https://careers.ey.com/search/?q=graduate&optionsFacetsDD_country=GB",
     "/ey/job/London-Audit-Graduate-SE1-2AF/1437484233/", "Audit Graduate"),
    ("https://www.kpmgcareers.co.uk/search/vacancies/?intakeType=Student&searchText=Graduate",
     "/Vacancies/GraduateTaxACAReadingAutumn2027/327123f0-7bd3-da84-d9a9-b5db2b87177b",
     "View role"),
    ("https://isw.changeworknow.co.uk/crowe/vms/e/careers/search/new",
     "/crowe/vms/e/careers/positions/diQDIzDDHjg60ZC9ITRdKc",
     "Assistant, Audit (Graduate), Manchester, UK - September 2027"),
    ("https://saffery.kallidusrecruit.com/Search.aspx",
     "VacancyInformation.aspx?VId=30479", "Graduate Personal Tax Trainee - Inverness"),
    ("https://careers.hmrc.gov.uk/jobs/search?query=graduate",
     "/jobs/graduate-valuation-surveyor-birmingham-united-kingdom", "Graduate Valuation Surveyor"),
]


@pytest.mark.parametrize("base,href,text", REAL_LINKS)
def test_job_links_recognise_real_careers_site_link_shapes(base, href, text):
    html = f'<a href="/about">About</a><a href="{href}">{text}</a>'
    [(found_text, url)] = job_links(html, base)
    assert found_text == text
    assert url == urljoin(base, href)


def test_job_links_treat_urls_differing_only_in_case_as_one_job():
    # KPMG links each vacancy twice, once with a lower-cased slug.
    html = """<a href="/Vacancies/GraduateTaxACAReading2027/327123f0">View role</a>
              <a href="/Vacancies/graduatetaxacareading2027/327123f0">View full job description</a>"""
    links = job_links(html, "https://www.kpmgcareers.co.uk/search/vacancies/")
    assert links == [("View role",
                      "https://www.kpmgcareers.co.uk/Vacancies/GraduateTaxACAReading2027/327123f0")]
