"""Opening a Store for a request should be cheap: the schema is applied once
per process, and request handlers reuse pooled connections."""

from uuid import uuid4

from jobs_agent.storage import open_store, store as store_mod
from jobs_agent.storage.store import Store


def test_the_schema_is_applied_once_per_process(monkeypatch):
    calls = []
    real = store_mod._apply_schema
    monkeypatch.setattr(store_mod, "_apply_schema",
                        lambda cur: (calls.append(1), real(cur))[1])
    schema = f"test_{uuid4().hex}"
    first = Store(user_id=str(uuid4()), schema=schema)
    second = Store(user_id=str(uuid4()), schema=schema)
    try:
        assert len(calls) == 1
        second.set_document("x", "works")   # tables exist for the second one too
    finally:
        first.conn.execute(f"DROP SCHEMA {schema} CASCADE")
        first.conn.commit()
        first.close()
        second.close()


def test_open_store_reuses_pooled_connections():
    from jobs_agent.config import database_url

    for _ in range(10):
        with open_store(user_id=str(uuid4())) as s:
            s.get_document("cv")
            conn = s.conn
        assert not conn.closed           # handed back to the pool, not closed
    opened = store_mod._pool(database_url()).get_stats().get("connections_num", 0)
    assert opened <= 2                   # ten requests, not ten connections


def test_a_failed_request_does_not_poison_the_pooled_connection():
    try:
        with open_store(user_id=str(uuid4())) as a:
            a.conn.execute("SELECT * FROM no_such_table")
    except Exception:
        pass
    with open_store(user_id=str(uuid4())) as b:
        assert b.get_document("cv") == ""
