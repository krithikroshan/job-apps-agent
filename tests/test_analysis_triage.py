"""The batch runner: which postings get analysed, what's sent, what's saved."""

import json

import pytest
from conftest import make_posting

from jobs_agent.analysis import triage
from jobs_agent.llm.base import LLMError
from jobs_agent.storage import DOC_CV


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete_with_source(self, system, messages, **kwargs):
        self.calls.append({"system": system, "messages": messages, **kwargs})
        if isinstance(self.reply, Exception):
            raise self.reply
        reply = self.reply(messages[-1].content) if callable(self.reply) else self.reply
        return reply, "Gemini (gemini-x)"


def answer_all(prompt):
    """Analyse every posting in the prompt as a decent entry-level fit."""
    ids = [int(line.split()[1].rstrip(":")) for line in prompt.splitlines()
           if line.startswith("POSTING ")]
    return json.dumps({"analyses": [
        {"id": i, "fit": 60 + i, "seniority": "entry", "visa": "not_mentioned"} for i in ids]})


def stage(store, n, **overrides):
    postings = [make_posting(title=f"Audit Trainee {i}", source_id=str(i),
                             description=f"Role number {i}. " * 5, score=40 + i, **overrides)
                for i in range(n)]
    store.upsert(postings)
    return [p.key for p in postings]


def test_one_batch_analyses_the_best_scored_postings_first(store):
    stage(store, 12)
    client = FakeClient(answer_all)
    result = triage.run_batch(store, client)
    assert result.analysed == triage.BATCH_SIZE
    assert result.remaining == 12 - triage.BATCH_SIZE
    prompt = client.calls[0]["messages"][-1].content
    assert "Audit Trainee 11" in prompt          # highest keyword score
    assert client.calls[0]["json_mode"] is True


def test_batches_run_until_nothing_is_left(store):
    stage(store, 10)
    client = FakeClient(answer_all)
    triage.run_batch(store, client)
    result = triage.run_batch(store, client)
    assert result.remaining == 0
    assert triage.run_batch(store, client).analysed == 0
    assert len(client.calls) == 2


def test_the_cv_and_profile_are_sent_and_the_posting_is_marked_as_data(store):
    store.set_document(DOC_CV, "BSc Accounting, audit internship at a mid-tier firm")
    stage(store, 1)
    client = FakeClient(answer_all)
    triage.run_batch(store, client)
    call = client.calls[0]
    assert "audit internship" in call["messages"][-1].content
    assert "untrusted" in call["system"].lower()


def test_changing_the_cv_makes_every_analysis_stale(store):
    stage(store, 3)
    triage.run_batch(store, FakeClient(answer_all))
    assert triage.status(store).remaining == 0
    store.set_document(DOC_CV, "a new CV")
    assert triage.status(store).remaining == 3


def test_rejected_and_submitted_postings_are_skipped(store):
    keys = stage(store, 3)
    store.set_status(keys[0], "rejected")
    store.set_status(keys[1], "submitted")
    assert triage.status(store).remaining == 1


def test_a_provider_failure_saves_nothing_and_says_why(store):
    stage(store, 2)
    with pytest.raises(LLMError):
        triage.run_batch(store, FakeClient(LLMError("every AI provider failed")))
    assert triage.status(store).remaining == 2


def test_postings_the_model_skips_are_retried_then_given_up_on(store):
    stage(store, 2)
    client = FakeClient('{"analyses": []}')
    for _ in range(triage.MAX_ATTEMPTS):
        triage.run_batch(store, client)
    assert triage.status(store).remaining == 0
    assert triage.status(store).failed == 2


def test_only_the_top_postings_are_ever_analysed(store, monkeypatch):
    monkeypatch.setattr(triage, "MAX_ANALYSED", 5)
    stage(store, 9)
    assert triage.status(store).remaining == 5


def test_the_saved_analysis_records_which_model_wrote_it(store):
    keys = stage(store, 1)
    triage.run_batch(store, FakeClient(answer_all))
    row = store.get_analysis(keys[0])
    assert row["model"] == "Gemini (gemini-x)"
    assert row["result"]["seniority"] == "entry"


def test_each_posting_is_fenced_with_a_marker_it_cannot_guess(store):
    stage(store, 2)
    client = FakeClient(answer_all)
    triage.run_batch(store, client)
    prompt = client.calls[0]["messages"][-1].content
    fence = [line for line in prompt.splitlines() if line.startswith("<<<")]
    assert fence and all(len(line) > 12 for line in fence)
    triage.run_batch(store, client)  # nothing left; but a new run would use a new marker


def test_a_visa_quote_not_found_in_the_posting_is_thrown_out(store):
    p = make_posting(description="Join our audit team. Full ACA support. " * 3)
    store.upsert([p])
    client = FakeClient(json.dumps({"analyses": [{
        "id": 1, "fit": 90, "visa": "offered",
        "visa_evidence": "We sponsor Skilled Worker visas for all graduates."}]}))
    triage.run_batch(store, client)
    result = store.get_analysis(p.key)["result"]
    assert result["visa"] == "not_mentioned"
    assert result["visa_evidence"] is None


def test_a_real_visa_quote_survives_light_paraphrase(store):
    p = make_posting(description="Great team. Unfortunately we are unable to offer visa "
                                 "sponsorship for this role. Apply now.")
    store.upsert([p])
    client = FakeClient(json.dumps({"analyses": [{
        "id": 1, "fit": 50, "visa": "not_offered",
        "visa_evidence": "We are unable to offer visa sponsorship for this role"}]}))
    triage.run_batch(store, client)
    assert store.get_analysis(p.key)["result"]["visa"] == "not_offered"


def test_only_inputs_the_analysis_reads_make_it_stale(store):
    from dataclasses import replace

    from jobs_agent.profile import load_profile, save_profile

    stage(store, 2)
    triage.run_batch(store, FakeClient(answer_all))
    profile = load_profile(store)
    save_profile(store, replace(profile, salary_bands=[[50000, 9]], contract_bonus=3,
                                title_blockers=["director"]))
    assert triage.status(store).remaining == 0
    save_profile(store, replace(profile, target_titles={"tax trainee": 30}))
    assert triage.status(store).remaining == 2
