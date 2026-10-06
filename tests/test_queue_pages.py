"""Paging through a stage, and counting what the filters let through."""

from conftest import make_posting

from jobs_agent.storage.analyses import AIFilters
from jobs_agent.web import api


def stage(store, n, **kw):
    postings = [make_posting(title=f"Audit Trainee {i}", source_id=str(i),
                             description=f"Posting number {i}. " * 4, score=10 + i, **kw)
                for i in range(n)]
    store.upsert(postings)
    return postings


def q(**params):
    return api.Request(query={k: [str(v)] for k, v in params.items()})


def test_offset_pages_through_in_match_order(store):
    stage(store, 7)
    first = [r["title"] for r in store.queue(limit=3)]
    second = [r["title"] for r in store.queue(limit=3, offset=3)]
    last = [r["title"] for r in store.queue(limit=3, offset=6)]
    assert first == ["Audit Trainee 6", "Audit Trainee 5", "Audit Trainee 4"]
    assert second == ["Audit Trainee 3", "Audit Trainee 2", "Audit Trainee 1"]
    assert last == ["Audit Trainee 0"]


def test_queue_count_applies_the_same_filters(store):
    stage(store, 5)
    store.upsert([make_posting(title="Tax Trainee", source_id="x", location="Leeds",
                               description="Different role entirely. " * 4, score=30)])
    assert store.queue_count() == 6
    assert store.queue_count(location="leeds") == 1
    assert store.queue_count(min_score=20) == len(list(store.queue(min_score=20, limit=500)))
    assert store.queue_count(status="shortlisted") == 0
    assert store.queue_count(ai=AIFilters(graduate_scheme=True)) == 0


def test_the_endpoint_returns_a_page_and_the_total(store):
    stage(store, 7)
    body = api.get_queue(store, q(limit=3, offset=3)).body
    assert body["total"] == 7
    assert (body["offset"], body["limit"]) == (3, 3)
    assert len(body["rows"]) == 3


def test_the_total_counts_filtered_results_not_the_whole_stage(store):
    stage(store, 4)
    store.upsert([make_posting(title="Tax Trainee", source_id="x", location="Leeds",
                               description="Different role entirely. " * 4)])
    body = api.get_queue(store, q(location="Leeds")).body
    assert body["total"] == 1 and len(body["rows"]) == 1


def test_page_size_and_offset_are_clamped(store):
    stage(store, 2)
    body = api.get_queue(store, q(limit=100000, offset=-5)).body
    assert body["limit"] == api.MAX_PAGE_SIZE
    assert body["offset"] == 0
    body = api.get_queue(store, q(limit=0)).body
    assert body["limit"] == 1
