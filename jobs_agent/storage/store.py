"""Postgres store. Deduplicates across sources and across runs, scoped to
one Supabase Auth user at a time."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool
from psycopg.rows import DictRow, dict_row

from ..config import database_url
from ..models import Posting
from .analyses import MATCH_SQL, AIFilters, AnalysisStore, match_score
from .companies import CompanyStore

__all__ = ["AIFilters", "Store", "match_score", "open_store"]

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Document ids used across the app. Kept here beside the schema comment that
# describes them, so adding one means touching a single place.
DOC_CV = "cv"
DOC_CV_FILENAME = "cv_filename"
DOC_TEMPLATE = "cover_letter_template"
DOC_CANDIDATE_NAME = "candidate_name"
DOC_SCORING_PROFILE = "scoring_profile"
DOC_LLM_SETTINGS = "llm_settings"


#: (dsn, schema) pairs whose tables this process has already created. The
#: schema is all CREATE ... IF NOT EXISTS, but each statement is a round
#: trip, and against a remote database that made every request a second
#: slower.
_SCHEMA_READY: set[tuple[str, str | None]] = set()
_SCHEMA_LOCK = threading.Lock()


def _apply_schema(cur) -> None:
    for statement in SCHEMA_PATH.read_text().split(";"):
        statement = statement.strip()
        if statement:
            cur.execute(statement)


class Store(AnalysisStore, CompanyStore):
    def __init__(self, dsn: str | None = None, *, user_id: str, schema: str | None = None,
                 conn: psycopg.Connection | None = None):
        """Connect to Postgres, scoped to ``user_id`` (a Supabase Auth user
        id). Every read and write this Store makes is filtered to, or
        tagged with, that user — the one place data segregation between
        accounts is enforced.

        ``schema`` isolates the tables under their own schema (rather than
        ``public``) instead of a separate database — used by tests to run in
        isolation against the same Supabase instance.

        ``conn`` is a connection to borrow (from :func:`open_store`'s pool);
        it's left open on :meth:`close`.
        """
        self.user_id = user_id
        self.schema = schema
        dsn = dsn or database_url()
        self._owns_conn = conn is None
        self.conn = conn or psycopg.connect(dsn, row_factory=dict_row)
        with self.conn.cursor() as cur:
            if schema:
                cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}")
                            .format(sql.Identifier(schema)))
                cur.execute(sql.SQL("SET search_path TO {}")
                            .format(sql.Identifier(schema)))
            with _SCHEMA_LOCK:
                if (dsn, schema) not in _SCHEMA_READY:
                    _apply_schema(cur)
                    _SCHEMA_READY.add((dsn, schema))
        self.conn.commit()

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- postings ---------------------------------------------------------

    def upsert(self, postings: Iterable[Posting]) -> tuple[int, int]:
        """Insert postings not seen before.

        Returns (new, duplicate). A posting is a duplicate if its source
        already gave a job with the same id, if its exact key is known, or if
        its soft_key is known AND the description overlaps enough that it is
        almost certainly the same role reposted.

        The source id comes first because the key hashes the description:
        a company-site posting (whose id is a hash of its URL) re-read with
        a description it lacked last time would otherwise come in again.
        """
        new = dup = 0
        now = datetime.utcnow().isoformat()
        cur = self.conn.cursor()
        uid = self.user_id

        for p in postings:
            if _has_source_id(p) and cur.execute(
                "SELECT 1 FROM postings WHERE user_id=%s AND source=%s AND source_id=%s",
                (uid, p.source, p.source_id),
            ).fetchone():
                dup += 1
                continue

            if cur.execute(
                "SELECT 1 FROM postings WHERE user_id=%s AND key=%s", (uid, p.key)
            ).fetchone():
                dup += 1
                continue

            soft_hits = cur.execute(
                "SELECT description FROM postings WHERE user_id=%s AND soft_key=%s",
                (uid, p.soft_key),
            ).fetchall()
            if any(_overlap(p.description, row["description"]) > 0.75 for row in soft_hits):
                dup += 1
                continue

            cur.execute(
                """INSERT INTO postings
                   (user_id, key, soft_key, source, source_id, title, employer,
                    location, description, url, posted, salary_min, salary_max,
                    contract_type, via_agency, score, score_reasons, first_seen)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uid, p.key, p.soft_key, p.source, p.source_id, p.title, p.employer,
                    p.location, p.description, p.url,
                    p.posted.isoformat() if p.posted else None,
                    p.salary_min, p.salary_max, p.contract_type,
                    int(p.via_agency) if p.via_agency is not None else None,
                    p.score, " | ".join(p.score_reasons), now,
                ),
            )
            cur.execute(
                "INSERT INTO applications (user_id, posting_key, status, updated) "
                "VALUES (%s, %s, 'new', %s) ON CONFLICT (user_id, posting_key) DO NOTHING",
                (uid, p.key, now),
            )
            new += 1

        self.conn.commit()
        return new, dup

    def _queue_sql(self, status: str, location: str | None, min_salary: float | None,
                   max_salary: float | None, contract_type: str | None,
                   ai: AIFilters | None, min_score: int) -> tuple[str, list]:
        """One stage's rows with every filter applied, as a subquery ``q``
        carrying ``match`` — shared by :meth:`queue` and :meth:`queue_count`
        so a page and its total can never disagree."""
        query = f"""SELECT * FROM (
                 SELECT p.*, a.status, a.letter, a.notes, a.updated,
                        x.result AS analysis, x.model AS analysis_model,
                        {MATCH_SQL} AS match
                 FROM postings p
                 JOIN applications a ON a.user_id = p.user_id AND a.posting_key = p.key
                 LEFT JOIN posting_analysis x
                   ON x.user_id = p.user_id AND x.posting_key = p.key
                 WHERE p.user_id = %s AND a.status = %s"""
        params: list = [self.user_id, status]
        if location:
            query += " AND p.location ILIKE %s"
            params.append(f"%{location}%")
        # A posting's pay is a range (salary_min/max), often with only one end
        # stated, so a comp filter checks for overlap against whichever end is
        # there rather than requiring both. Nothing stated at all can't be
        # known to overlap, so it's excluded once either bound is filtered on.
        if min_salary is not None:
            query += " AND COALESCE(p.salary_max, p.salary_min) >= %s"
            params.append(min_salary)
        if max_salary is not None:
            query += " AND COALESCE(p.salary_min, p.salary_max) <= %s"
            params.append(max_salary)
        if contract_type:
            query += " AND p.contract_type = %s"
            params.append(contract_type)
        if ai:
            ai_sql, ai_params = ai.sql()
            query += ai_sql
            params += ai_params
        query += ") q WHERE q.match >= %s"
        params.append(min_score)
        return query, params

    def queue(self, min_score: int = 0, limit: int = 50,
              status: str = "new", location: str | None = None,
              min_salary: float | None = None,
              max_salary: float | None = None,
              contract_type: str | None = None,
              ai: AIFilters | None = None, offset: int = 0) -> Iterator[DictRow]:
        """One page of a stage of the queue, best Match first. Each row
        carries ``match`` (0-100, see :func:`match_score`) and ``analysis``
        (the AI's result, or None); ``min_score`` filters on Match."""
        query, params = self._queue_sql(status, location, min_salary, max_salary,
                                        contract_type, ai, min_score)
        # key breaks ties so pages are stable: equal matches and fetch times
        # would otherwise shuffle between requests and repeat or skip rows.
        query += " ORDER BY q.match DESC, q.first_seen DESC, q.key LIMIT %s OFFSET %s"
        yield from self.conn.execute(query, params + [limit, max(0, offset)])

    def queue_count(self, min_score: int = 0, status: str = "new",
                    location: str | None = None, min_salary: float | None = None,
                    max_salary: float | None = None, contract_type: str | None = None,
                    ai: AIFilters | None = None) -> int:
        """How many rows :meth:`queue` would page through with these filters."""
        query, params = self._queue_sql(status, location, min_salary, max_salary,
                                        contract_type, ai, min_score)
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM ({query}) counted", params).fetchone()
        return row["n"]

    def get_posting(self, key: str) -> DictRow | None:
        return self.conn.execute(
            "SELECT * FROM postings WHERE user_id=%s AND key=%s", (self.user_id, key)
        ).fetchone()

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) c FROM applications WHERE user_id=%s GROUP BY status",
            (self.user_id,),
        ).fetchall()
        return {r["status"]: r["c"] for r in rows}

    # -- applications -----------------------------------------------------

    def get_application(self, key: str) -> DictRow | None:
        return self.conn.execute(
            "SELECT * FROM applications WHERE user_id=%s AND posting_key=%s",
            (self.user_id, key),
        ).fetchone()

    def set_status(self, key: str, status: str, notes: str | None = None) -> None:
        self.conn.execute(
            "UPDATE applications SET status=%s, notes=COALESCE(%s, notes), updated=%s "
            "WHERE user_id=%s AND posting_key=%s",
            (status, notes, datetime.utcnow().isoformat(), self.user_id, key),
        )
        self.conn.commit()

    def set_letter(self, key: str, letter: str) -> None:
        """Save/edit the drafted letter text without touching status."""
        self.conn.execute(
            "UPDATE applications SET letter=%s, updated=%s "
            "WHERE user_id=%s AND posting_key=%s",
            (letter, datetime.utcnow().isoformat(), self.user_id, key),
        )
        self.conn.commit()

    def delete_posting(self, key: str) -> bool:
        """Remove a posting and its application row for good — unlike
        "rejected" (still in the queue, just set aside), this is permanent.

        Deletes the application row first: it has a foreign key on the
        posting.
        """
        cur = self.conn.cursor()
        # Explicit as well as cascaded: a table created before the cascade
        # was added wouldn't have it.
        cur.execute(
            "DELETE FROM posting_analysis WHERE user_id=%s AND posting_key=%s",
            (self.user_id, key),
        )
        cur.execute(
            "DELETE FROM applications WHERE user_id=%s AND posting_key=%s",
            (self.user_id, key),
        )
        cur.execute(
            "DELETE FROM postings WHERE user_id=%s AND key=%s",
            (self.user_id, key),
        )
        deleted = cur.rowcount > 0
        self.conn.commit()
        return deleted

    # -- documents and files ----------------------------------------------

    def get_document(self, doc_id: str) -> str:
        row = self.conn.execute(
            "SELECT content FROM documents WHERE user_id=%s AND id=%s",
            (self.user_id, doc_id),
        ).fetchone()
        return row["content"] if row else ""

    def set_document(self, doc_id: str, content: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO documents (user_id, id, content, updated) VALUES (%s, %s, %s, %s)
               ON CONFLICT(user_id, id) DO UPDATE
                 SET content=excluded.content, updated=excluded.updated""",
            (self.user_id, doc_id, content, now),
        )
        self.conn.commit()

    def get_file(self, file_id: str) -> DictRow | None:
        return self.conn.execute(
            "SELECT id, filename, data, updated FROM files WHERE user_id=%s AND id=%s",
            (self.user_id, file_id),
        ).fetchone()

    def set_file(self, file_id: str, filename: str, data: bytes) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO files (user_id, id, filename, data, updated)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT(user_id, id) DO UPDATE SET
                 filename=excluded.filename, data=excluded.data, updated=excluded.updated""",
            (self.user_id, file_id, filename, data, now),
        )
        self.conn.commit()

    # -- user secrets (always ciphertext; see crypto.py) --------------------

    def get_secret(self, provider: str) -> DictRow | None:
        return self.conn.execute(
            "SELECT provider, ciphertext, last4, updated FROM user_secrets "
            "WHERE user_id=%s AND provider=%s",
            (self.user_id, provider),
        ).fetchone()

    def list_secrets(self) -> list[DictRow]:
        """Which providers have a key on file — without the ciphertext."""
        return self.conn.execute(
            "SELECT provider, last4, updated FROM user_secrets WHERE user_id=%s",
            (self.user_id,),
        ).fetchall()

    def set_secret(self, provider: str, ciphertext: str, last4: str) -> None:
        now = datetime.utcnow().isoformat()
        self.conn.execute(
            """INSERT INTO user_secrets (user_id, provider, ciphertext, last4, updated)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT(user_id, provider) DO UPDATE SET
                 ciphertext=excluded.ciphertext, last4=excluded.last4,
                 updated=excluded.updated""",
            (self.user_id, provider, ciphertext, last4, now),
        )
        self.conn.commit()

    def delete_secret(self, provider: str) -> None:
        self.conn.execute(
            "DELETE FROM user_secrets WHERE user_id=%s AND provider=%s",
            (self.user_id, provider),
        )
        self.conn.commit()


@contextmanager
def open_store(dsn: str | None = None, *, user_id: str,
               schema: str | None = None) -> Iterator[Store]:
    """``with open_store(dsn, user_id=...) as store:`` — closes on every exit
    path.

    The request handlers return early a dozen different ways; relying on each
    of them to remember ``store.close()`` was a connection leak waiting to
    happen.

    Connections come from a per-process pool: opening a fresh one to a
    remote database costs about half a second, on every request. (Tests'
    isolated ``schema`` stores set a search_path, so they get their own.)
    """
    if schema:
        store = Store(dsn, user_id=user_id, schema=schema)
        try:
            yield store
        finally:
            store.close()
        return
    # The pool's context rolls back anything a failed request left open
    # before handing the connection to the next one.
    with _pool(dsn or database_url()).connection() as conn:
        yield Store(dsn, user_id=user_id, conn=conn)


_POOLS: dict[str, ConnectionPool] = {}
_POOLS_LOCK = threading.Lock()
#: Supabase's session pooler (port 5432) allows only 15 clients in total,
#: shared by every Vercel instance, a local server and the tests, so each
#: process keeps few and lets idle ones go. (Its transaction pooler, port
#: 6543, multiplexes clients and suits serverless better; see README.)
POOL_MAX = 3
#: Seconds an unused connection stays open before the pool closes it.
POOL_MAX_IDLE = 60


def _pool(dsn: str) -> ConnectionPool:
    with _POOLS_LOCK:
        pool = _POOLS.get(dsn)
        if pool is None:
            pool = ConnectionPool(
                dsn, min_size=0, max_size=POOL_MAX, max_idle=POOL_MAX_IDLE,
                open=True,
                # No server-side prepared statements: a transaction pooler
                # hands each transaction to whichever backend is free.
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
                # A connection the database dropped while idle is replaced
                # rather than handed to a request.
                check=ConnectionPool.check_connection,
            )
            _POOLS[dsn] = pool
        return pool


def _has_source_id(p: Posting) -> bool:
    """Whether the source gave a real id. Adapters stringify a missing one
    as "None"; that mustn't make every such job the same job."""
    return p.source_id not in ("", "None")


def _overlap(a: str, b: str) -> float:
    """Jaccard overlap on word sets. Crude, cheap, good enough for reposts."""
    if not a or not b:
        return 0.0
    sa, sb = set(a.lower().split()), set(b.lower().split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
