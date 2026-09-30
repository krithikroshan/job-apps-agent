"""Store: dedupe on insert, and the document/file round-trips."""

from uuid import uuid4

from conftest import make_posting

from jobs_agent.storage import Store


def test_exact_duplicate_is_suppressed(store):
    assert store.upsert([make_posting()]) == (1, 0)
    assert store.upsert([make_posting()]) == (0, 1)


def test_repost_with_a_lightly_edited_body_is_suppressed(store):
    """Same title and location, body overlapping above the 0.75 threshold."""
    store.upsert([make_posting()])
    tweaked = make_posting(
        employer="Another Agency",
        description="Sanctions screening and due diligence for a law graduate. "
                    "Immediate start.",
    )
    assert store.upsert([tweaked]) == (0, 1)


def test_a_heavily_rewritten_body_is_not_suppressed(store):
    """Below the threshold it counts as a new posting -- the soft key alone
    isn't enough to merge two roles."""
    store.upsert([make_posting()])
    rewritten = make_posting(description="Drafting contracts for a retail client.")
    assert store.upsert([rewritten]) == (1, 0)


def test_a_genuinely_different_role_is_kept(store):
    store.upsert([make_posting()])
    other = make_posting(title="Paralegal", description="Bundling and disclosure work.")
    assert store.upsert([other]) == (1, 0)


def test_upsert_creates_a_new_application(store):
    p = make_posting()
    store.upsert([p])
    assert store.get_application(p.key)["status"] == "new"
    assert store.stats() == {"new": 1}


def test_queue_filters_by_status_and_location(store):
    p = make_posting(score=50)
    store.upsert([p])
    assert len(list(store.queue(status="new", location="London"))) == 1
    assert len(list(store.queue(status="new", location="Leeds"))) == 0
    assert len(list(store.queue(status="approved"))) == 0


def test_queue_filters_by_compensation_range(store):
    # Distinct titles/descriptions so the soft-dedupe in upsert() doesn't
    # collapse these into one posting.
    store.upsert([make_posting(
        title="Compliance Analyst", description="Sanctions screening role.",
        salary_min=40000, salary_max=50000,
    )])
    store.upsert([make_posting(
        title="Senior Compliance Manager", description="Leads the AML team.",
        source_id="2", salary_min=70000, salary_max=90000,
    )])
    store.upsert([make_posting(
        title="Paralegal", description="Bundling and disclosure work.",
        source_id="3", salary_min=None, salary_max=None,
    )])

    assert len(list(store.queue(min_salary=60000))) == 1
    assert len(list(store.queue(max_salary=55000))) == 1
    assert len(list(store.queue(min_salary=45000, max_salary=80000))) == 2
    assert len(list(store.queue(min_salary=100000))) == 0
    assert len(list(store.queue())) == 3


def test_queue_filters_by_contract_type(store):
    store.upsert([make_posting(
        title="Compliance Analyst", description="Sanctions screening role.",
        contract_type="permanent",
    )])
    store.upsert([make_posting(
        title="Senior Compliance Manager", description="Leads the AML team.",
        source_id="2", contract_type="contract",
    )])
    store.upsert([make_posting(
        title="Paralegal", description="Bundling and disclosure work.",
        source_id="3", contract_type=None,
    )])

    assert len(list(store.queue(contract_type="permanent"))) == 1
    assert len(list(store.queue(contract_type="contract"))) == 1
    assert len(list(store.queue())) == 3


def test_delete_posting_removes_it_and_its_application(store):
    p = make_posting()
    store.upsert([p])
    assert store.delete_posting(p.key) is True
    assert store.get_posting(p.key) is None
    assert store.get_application(p.key) is None
    assert store.stats() == {}


def test_delete_unknown_posting_is_a_noop(store):
    assert store.delete_posting("nope") is False


def test_documents_and_files_overwrite_in_place(store):
    store.set_document("cv", "first")
    store.set_document("cv", "second")
    assert store.get_document("cv") == "second"
    assert store.get_document("never-written") == ""

    store.set_file("cv", "cv.pdf", b"%PDF-1.4")
    store.set_file("cv", "cv2.pdf", b"%PDF-1.7")
    row = store.get_file("cv")
    assert row["filename"] == "cv2.pdf"
    assert bytes(row["data"]) == b"%PDF-1.7"


def test_data_is_isolated_between_users(store):
    """A second account sharing the same tables sees none of the first
    user's postings, applications, documents, or files."""
    p = make_posting()
    store.upsert([p])
    store.set_document("cv", "first user's cv text")
    store.set_file("cv", "cv.pdf", b"%PDF-1.4")

    other = Store(user_id=str(uuid4()), schema=store.schema)
    try:
        assert list(other.queue()) == []
        assert other.get_posting(p.key) is None
        assert other.get_application(p.key) is None
        assert other.stats() == {}
        assert other.get_document("cv") == ""
        assert other.get_file("cv") is None
    finally:
        other.close()
