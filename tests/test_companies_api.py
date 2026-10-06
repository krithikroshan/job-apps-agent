"""Companies endpoints and page. The careers site is never fetched: checks
replace ``pipeline._fetch``, and DNS is stubbed for URLs that must pass."""

import pytest

from jobs_agent.companies import catalog, pipeline
from jobs_agent.presets import get_preset
from jobs_agent.profile import save_profile
from jobs_agent.storage.companies import MAX_COMPANIES
from jobs_agent.web import api, api_companies, handler, pages


def req(**payload):
    return api.Request(payload=payload)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr("jobs_agent.careers.net._resolve", lambda host: ["93.184.216.34"])


@pytest.fixture
def site(monkeypatch):
    """What every careers site 'returns' when checked."""
    holder = {"jobs": []}

    def fake(site, search_terms):
        if isinstance(holder["jobs"], Exception):
            raise holder["jobs"]
        return holder["jobs"]
    monkeypatch.setattr(pipeline, "_fetch", fake)
    return holder


def add(store, name="Monzo", url="https://boards.greenhouse.io/monzo"):
    return api_companies.post_company(store, req(name=name, careers_url=url))


# -- adding ----------------------------------------------------------------

def test_add_detects_the_system(store):
    res = add(store)
    assert res.status == 200
    c = res.body["company"]
    assert (c["name"], c["ats"], c["slug"]) == ("Monzo", "greenhouse", "monzo")
    assert c["ats_label"] == "Greenhouse" and c["host"] == "boards.greenhouse.io"
    assert "user_id" not in c


def test_a_bare_domain_gets_https(store):
    res = add(store, "Acme", "www.example.co.uk/careers")
    assert res.status == 200
    assert res.body["company"]["careers_url"] == "https://www.example.co.uk/careers"
    assert res.body["company"]["ats"] == "generic"


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://169.254.169.254/latest/",
                                 "javascript:alert(1)", "http://10.0.0.5/careers"])
def test_an_unsafe_url_is_rejected(store, url):
    res = add(store, "Evil", url)
    assert res.status == 400
    assert "can't be used" in res.body["error"]
    assert store.list_companies() == []


@pytest.mark.parametrize("payload", [
    {}, {"name": "Monzo"}, {"careers_url": "https://boards.greenhouse.io/monzo"},
    {"name": "  ", "careers_url": "https://boards.greenhouse.io/monzo"},
    {"name": "x" * 200, "careers_url": "https://boards.greenhouse.io/monzo"},
    {"name": "Monzo", "careers_url": "https://example.com/" + "a" * 600},
    {"name": ["Monzo"], "careers_url": "https://boards.greenhouse.io/monzo"},
])
def test_add_validates_its_input(store, payload):
    assert api_companies.post_company(store, req(**payload)).status == 400


def test_a_duplicate_is_refused(store):
    add(store)
    res = add(store, "Monzo again")
    assert res.status == 400 and "already" in res.body["error"]


def test_the_cap_is_enforced_and_reported(store):
    for n in range(MAX_COMPANIES):
        assert add(store, f"Firm {n}", f"https://boards.greenhouse.io/firm{n}").status == 200
    assert api_companies.get_companies(store, api.Request()).body["can_add"] is False
    assert add(store, "One more", "https://boards.greenhouse.io/more").status == 400


# -- listing and suggestions -------------------------------------------------

def test_list_carries_suggestions_for_the_preset_minus_ones_added(store, monkeypatch):
    save_profile(store, get_preset("accounting_graduate").profile)
    monkeypatch.setitem(catalog.SUGGESTED, "accounting_graduate", [
        ("Monzo", "https://boards.greenhouse.io/monzo"),
        ("Wise", "https://job-boards.eu.greenhouse.io/wise"),
    ])
    add(store)
    body = api_companies.get_companies(store, api.Request()).body
    assert [c["name"] for c in body["companies"]] == ["Monzo"]
    assert body["suggested"] == [{"name": "Wise",
                                  "careers_url": "https://job-boards.eu.greenhouse.io/wise"}]
    assert body["can_add"] is True and body["max"] == MAX_COMPANIES
    assert body["due"] == 1 and body["next_due"]["name"] == "Monzo"


def test_no_preset_means_no_suggestions(store):
    assert api_companies.get_companies(store, api.Request()).body["suggested"] == []


def test_every_catalog_entry_is_well_formed():
    for preset, entries in catalog.SUGGESTED.items():
        assert get_preset(preset), preset
        for name, url in entries:
            assert name.strip() and url.startswith("https://")
        assert len({n.lower() for n, _ in entries}) == len(entries), preset
        assert len({u.lower() for _, u in entries}) == len(entries), preset


# -- removing --------------------------------------------------------------

def test_delete(store):
    cid = add(store).body["company"]["id"]
    assert api_companies.post_company_delete(store, req(id=cid)).status == 200
    assert store.list_companies() == []
    assert api_companies.post_company_delete(store, req(id=cid)).status == 404
    assert api_companies.post_company_delete(store, req()).status == 400


# -- checking ----------------------------------------------------------------

def test_check_runs_the_next_due_company(store, site):
    save_profile(store, get_preset("accounting_graduate").profile)
    add(store)
    add(store, "Wise", "https://job-boards.eu.greenhouse.io/wise")
    site["jobs"] = [{"title": "Graduate Audit Associate", "url": "https://x.com/1",
                     "location": "London", "description": "ACA study support.",
                     "posted": None, "employment_type": "FULL_TIME"}]
    first = api_companies.post_company_check(store, req())
    assert first.status == 200
    assert first.body["company"]["name"] == "Monzo"
    assert first.body["result"]["new"] == 1
    assert first.body["remaining"] == 1 and first.body["next_due"]["name"] == "Wise"
    second = api_companies.post_company_check(store, req())
    assert second.body["company"]["name"] == "Wise" and second.body["remaining"] == 0
    third = api_companies.post_company_check(store, req())
    assert third.status == 200
    assert third.body["company"] is None and third.body["remaining"] == 0


def test_check_one_by_id_even_if_checked_hours_ago(store, site):
    cid = add(store).body["company"]["id"]
    api_companies.post_company_check(store, req(id=cid))
    store.conn.execute("UPDATE companies SET last_checked = '2020-01-01T00:00:00+00:00'")
    store.conn.commit()
    res = api_companies.post_company_check(store, req(id=cid))
    assert res.status == 200 and res.body["company"]["id"] == cid
    assert not res.body.get("cooldown")
    assert res.body["company"]["last_checked"] > "2020"
    assert api_companies.post_company_check(store, req(id="nope")).status == 404


def test_a_recheck_within_minutes_returns_the_last_result(store, site):
    fetches = []
    site["jobs"] = []
    cid = add(store).body["company"]["id"]
    first = api_companies.post_company_check(store, req(id=cid))
    original = pipeline._fetch
    pipeline._fetch = lambda *a: fetches.append(a) or original(*a)
    try:
        res = api_companies.post_company_check(store, req(id=cid))
    finally:
        pipeline._fetch = original
    assert res.status == 200 and res.body["cooldown"] is True
    assert fetches == []  # the site wasn't read again
    assert res.body["result"]["found"] == first.body["result"]["found"]
    assert res.body["result"]["error"] == first.body["result"]["error"]
    assert "minutes" in res.body["message"]


def test_checks_are_capped_per_day(store, site, monkeypatch):
    monkeypatch.setenv("JOBS_AGENT_COMPANY_CHECKS_PER_DAY", "2")
    add(store)
    add(store, "Wise", "https://job-boards.eu.greenhouse.io/wise")
    add(store, "Revolut", "https://boards.greenhouse.io/revolut")
    assert api_companies.post_company_check(store, req()).status == 200
    assert api_companies.post_company_check(store, req()).status == 200
    res = api_companies.post_company_check(store, req())
    assert res.status == 429 and "tomorrow" in res.body["error"]
    assert store.get_company(store.next_company_to_check()["id"])["last_checked"] is None


@pytest.mark.parametrize("raw,expected", [
    ("", api_companies.DEFAULT_CHECKS_PER_DAY), ("junk", api_companies.DEFAULT_CHECKS_PER_DAY),
    ("5", 5), ("-3", 0),
])
def test_the_daily_check_cap_reads_the_environment(monkeypatch, raw, expected):
    monkeypatch.setenv("JOBS_AGENT_COMPANY_CHECKS_PER_DAY", raw)
    assert api_companies.checks_per_day() == expected


def test_a_site_failure_is_a_200_with_the_message(store, site):
    add(store)
    site["jobs"] = RuntimeError("boom")
    res = api_companies.post_company_check(store, req())
    assert res.status == 200
    assert res.body["result"]["error"]
    assert res.body["company"]["last_error"] == res.body["result"]["error"]


def test_a_check_already_running_is_a_409(store, site):
    add(store)
    with store.exclusive("companies"):
        res = api_companies.post_company_check(store, req())
    assert res.status == 409


# -- routing and the page ----------------------------------------------------

def test_routes_are_registered():
    assert handler.GET_ROUTES["/api/companies"] is api_companies.get_companies
    assert handler.POST_ROUTES["/api/companies"] is api_companies.post_company
    assert handler.POST_ROUTES["/api/companies/delete"] is api_companies.post_company_delete
    assert handler.POST_ROUTES["/api/companies/check"] is api_companies.post_company_check


def test_nav_has_companies_between_profile_and_settings():
    nav = pages.nav("companies")
    assert 'href="/companies" aria-current="page"' in nav
    assert nav.index('href="/documents"') < nav.index('href="/companies"') < nav.index(
        'href="/settings"')


def test_companies_page_renders_and_escapes_the_name():
    html = pages.companies_page('<script>alert("x")</script>')
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "{nav}" not in html
    assert 'id="add-company"' in html and 'src="/static/companies.js"' in html
    assert "robots.txt" not in html


def test_companies_assets_resolve():
    for name in ("companies.js", "companies.css", "logo-careers.svg"):
        assert pages.static_asset(name) is not None, name


def test_the_queue_labels_company_site_postings():
    js = pages.static_asset("queue.js")[0].decode()
    assert 'careers: { label: "the company\'s site", logo: "/static/logo-careers.svg" }' in js
