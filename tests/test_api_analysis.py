"""Analysis and suggestion endpoints. The AI client is always a stub."""

import json

import pytest
from conftest import make_posting

from jobs_agent.llm.base import LLMError
from jobs_agent.profile import load_profile
from jobs_agent.storage import DOC_CV
from jobs_agent.web import api, api_ai


def req(**payload):
    return api.Request(payload=payload)


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete_with_source(self, system, messages, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply, "Stub (stub-1)"

    def complete(self, system, messages, **kwargs):
        return self.complete_with_source(system, messages, **kwargs)[0]


@pytest.fixture
def client(monkeypatch):
    holder = {}

    def use(reply):
        holder["client"] = FakeClient(reply)
        return holder["client"]

    monkeypatch.setattr(api_ai.llm, "for_user", lambda store, fast=False: holder["client"])
    return use


# -- analysis ------------------------------------------------------------------

def test_status_counts_whats_left(store):
    store.upsert([make_posting()])
    body = api_ai.get_analysis_status(store, api.Request()).body
    assert body == {"remaining": 1, "analysed": 0, "failed": 0}


def test_analyse_runs_one_batch(store, client):
    p = make_posting()
    store.upsert([p])
    client(json.dumps({"analyses": [{"id": 1, "fit": 77}]}))
    res = api_ai.post_analyse(store, req())
    assert res.status == 200
    assert res.body["analysed"] == 1 and res.body["remaining"] == 0
    assert store.get_analysis(p.key)["result"]["fit"] == 77


def test_analyse_reports_a_provider_failure(store, client):
    store.upsert([make_posting()])
    client(LLMError("No AI provider is set up. Add an API key on the Settings page."))
    res = api_ai.post_analyse(store, req())
    assert res.status == 400
    assert "Settings" in res.body["error"]


def test_the_queue_carries_match_and_analysis(store):
    p = make_posting(score=40)
    store.upsert([p])
    store.save_analysis(p.key, "ctx", "done", {"fit": 90, "visa": "offered"}, "m", None)
    rows = api.get_queue(store, api.Request(query={"visa": ["offered"]})).body
    assert rows[0]["analysis"]["fit"] == 90
    assert rows[0]["match"] > 40


@pytest.mark.parametrize("query", [
    {"visa": ["teleport"]}, {"level": ["wizard"]}, {"max_years": ["-1"]},
])
def test_unknown_ai_filter_values_are_ignored(store, query):
    store.upsert([make_posting()])
    assert api.get_queue(store, api.Request(query=query)).status == 200


# -- suggestions ------------------------------------------------------------------

PROPOSAL = {"reply": "Based on your CV I'd target audit and tax trainee roles.",
            "proposal": {"target_titles": {"audit trainee": 30, "tax trainee": 26}}}


def test_suggest_from_cv_needs_a_cv(store, client):
    client(json.dumps(PROPOSAL))
    res = api_ai.post_suggest_profile(store, req(source="cv"))
    assert res.status == 400
    assert "CV" in res.body["error"]


def test_suggest_from_cv_returns_a_preview_and_saves_nothing(store, client):
    store.set_document(DOC_CV, "BSc Accounting and Finance. Audit internship.")
    stub = client(json.dumps(PROPOSAL))
    before = load_profile(store)
    res = api_ai.post_suggest_profile(store, req(source="cv"))
    assert res.status == 200
    assert res.body["proposal"] == PROPOSAL["proposal"]
    assert "audit trainee = 30" in res.body["preview"]["target_titles"]
    assert load_profile(store) == before
    assert "Audit internship" in stub.calls[0]["messages"][-1].content


def test_suggest_from_history_needs_some_decisions(store, client):
    client(json.dumps(PROPOSAL))
    res = api_ai.post_suggest_profile(store, req(source="history"))
    assert res.status == 400


def test_suggest_from_history_sends_shortlisted_and_rejected_titles(store, client):
    keep = make_posting(title="Graduate Audit Associate", source_id="1", description="a " * 40)
    drop = make_posting(title="Payroll Administrator", source_id="2", description="b " * 40)
    drop2 = make_posting(title="Credit Controller", source_id="3", description="c " * 40)
    store.upsert([keep, drop, drop2])
    store.set_status(keep.key, "shortlisted")
    store.set_status(drop.key, "rejected")
    store.set_status(drop2.key, "rejected")
    stub = client(json.dumps(PROPOSAL))
    res = api_ai.post_suggest_profile(store, req(source="history"))
    assert res.status == 200
    prompt = stub.calls[0]["messages"][-1].content
    assert "Graduate Audit Associate" in prompt and "Payroll Administrator" in prompt


def test_unknown_suggestion_source_is_a_400(store, client):
    assert api_ai.post_suggest_profile(store, req(source="vibes")).status == 400


def test_search_is_turned_into_validated_filters(store, client):
    client(json.dumps({"filters": {
        "location": "Manchester", "min_salary": 25000, "contract_type": "permanent",
        "visa": "offered", "level": "entry", "graduate_scheme": True,
        "max_years": 1, "teleport": True, "min_score": "lots",
    }, "note": "Graduate audit schemes in Manchester that sponsor visas."}))
    res = api_ai.post_search(store, req(query="audit grad schemes in manchester with visa"))
    assert res.status == 200
    f = res.body["filters"]
    assert f["location"] == "Manchester" and f["visa"] == "offered"
    assert f["graduate_scheme"] is True and f["max_years"] == 1
    assert "teleport" not in f and "min_score" not in f
    assert res.body["note"]


@pytest.mark.parametrize("query", ["", "   ", "x" * 600])
def test_search_needs_a_sensible_query(store, client, query):
    client("{}")
    assert api_ai.post_search(store, req(query=query)).status == 400


@pytest.mark.parametrize("years", ["²", "٣", "1.5", "99"])
def test_odd_max_years_values_are_ignored_not_a_500(store, years):
    store.upsert([make_posting()])
    assert api.get_queue(store, api.Request(query={"max_years": [years]})).status == 200


def test_analyse_refuses_a_second_concurrent_run(store, client):
    store.upsert([make_posting()])
    client(json.dumps({"analyses": []}))
    with store.exclusive("analyse"):
        res = api_ai.post_analyse(store, req())
    assert res.status == 409


def test_a_search_with_salaries_backwards_is_put_right(store, client):
    client(json.dumps({"filters": {"min_salary": 40000, "max_salary": 25000}}))
    f = api_ai.post_search(store, req(query="between 40k and 25k")).body["filters"]
    assert (f["min_salary"], f["max_salary"]) == (25000, 40000)


def test_retrying_gives_failed_postings_another_go(store, client):
    from jobs_agent.analysis import triage

    store.upsert([make_posting()])
    client(json.dumps({"analyses": []}))
    for _ in range(triage.MAX_ATTEMPTS):
        api_ai.post_analyse(store, req())
    assert api_ai.get_analysis_status(store, api.Request()).body["failed"] == 1
    res = api_ai.post_analyse_retry(store, req())
    assert res.status == 200
    assert res.body == {"remaining": 1, "analysed": 0, "failed": 0}
