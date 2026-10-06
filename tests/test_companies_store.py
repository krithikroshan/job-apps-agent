"""The watched-companies table: per-user, capped, and checked oldest first."""

from uuid import uuid4

import pytest

from jobs_agent.storage import Store
from jobs_agent.storage.companies import MAX_COMPANIES, CompanyError, company_id


def add(store, n, **kw):
    return store.add_company(name=kw.get("name", f"Firm {n}"),
                             careers_url=kw.get("url", f"https://boards.greenhouse.io/firm{n}"),
                             ats="greenhouse", slug=f"firm{n}")


def test_add_and_list(store):
    row = add(store, 1, name="Monzo", url="https://boards.greenhouse.io/monzo")
    assert row["id"] == company_id("https://boards.greenhouse.io/monzo")
    assert row["name"] == "Monzo" and row["ats"] == "greenhouse"
    assert row["last_checked"] is None
    listed = store.list_companies()
    assert [c["name"] for c in listed] == ["Monzo"]
    assert store.get_company(row["id"])["careers_url"] == "https://boards.greenhouse.io/monzo"


def test_the_same_url_twice_is_refused(store):
    add(store, 1)
    with pytest.raises(CompanyError, match="already"):
        add(store, 1, name="Another name")


def test_the_list_is_capped(store):
    for n in range(MAX_COMPANIES):
        add(store, n)
    with pytest.raises(CompanyError, match=str(MAX_COMPANIES)):
        add(store, 999)
    assert len(store.list_companies()) == MAX_COMPANIES


def test_delete(store):
    row = add(store, 1)
    assert store.delete_company(row["id"]) is True
    assert store.delete_company(row["id"]) is False
    assert store.get_company(row["id"]) is None


def test_companies_are_isolated_between_users(store):
    row = add(store, 1)
    other = Store(user_id=str(uuid4()), schema=store.schema)
    try:
        assert other.list_companies() == []
        assert other.get_company(row["id"]) is None
        assert other.delete_company(row["id"]) is False
        assert other.next_company_to_check() is None
        # The same URL is free for another account: uniqueness is per user.
        add(other, 1)
        assert len(other.list_companies()) == 1
    finally:
        other.close()
    assert store.get_company(row["id"]) is not None


def test_never_checked_comes_first_then_oldest(store):
    a, b, c = add(store, 1), add(store, 2), add(store, 3)
    store.record_company_check(a["id"], found=5, new=1, error=None)
    store.record_company_check(c["id"], found=0, new=0, error="Couldn't reach the site.")
    assert store.next_company_to_check()["id"] == b["id"]
    store.record_company_check(b["id"], found=2, new=2, error=None)
    assert store.next_company_to_check()["id"] == a["id"]


def test_record_check_and_due_count(store):
    a, b = add(store, 1), add(store, 2)
    store.record_company_check(a["id"], found=12, new=2, error=None)
    row = store.get_company(a["id"])
    assert (row["last_found"], row["last_new"], row["last_error"]) == (12, 2, None)
    assert row["last_checked"]
    # Only b hasn't been checked since the cut-off.
    cutoff = "2000-01-01T00:00:00+00:00"
    assert store.count_companies_due(cutoff) == 1
    assert store.next_company_to_check(due_before=cutoff)["id"] == b["id"]
    store.record_company_check(b["id"], found=0, new=0, error="boom")
    assert store.count_companies_due(cutoff) == 0
    assert store.next_company_to_check(due_before=cutoff) is None
    assert store.get_company(b["id"])["last_error"] == "boom"


def test_adding_waits_for_another_add_in_progress(store):
    """The count and the insert happen under one per-user lock, so two
    tabs adding at once can't both slip under the cap."""
    import threading

    other = Store(user_id=store.user_id, schema=store.schema)
    try:
        other.conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                           (f"{store.user_id}:companies-add",))
        done = threading.Event()
        worker = threading.Thread(target=lambda: (add(store, 1), done.set()))
        worker.start()
        assert not done.wait(0.3)  # blocked behind the other add
        other.conn.commit()        # releases the transaction lock
        assert done.wait(5)
        worker.join()
    finally:
        other.close()
    assert len(store.list_companies()) == 1


def test_take_quota_counts_each_kind_separately(store):
    assert store.take_quota("company-check", 2) is True
    assert store.take_quota("company-check", 2) is True
    assert store.take_quota("company-check", 2) is False
    # The AI meter is the same table under another kind, unaffected.
    assert store.take_ai_call(1) is True
    assert store.take_ai_call(1) is False
