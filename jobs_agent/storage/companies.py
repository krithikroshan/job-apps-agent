"""Storage for the companies a user watches: careers sites read as a job
source alongside the job boards (see ``companies/``). Mixed into
:class:`~jobs_agent.storage.store.Store`, so every query is scoped to the
store's user like the rest.

Each company is checked one per request, oldest first — a serverless
request can't afford to read forty careers sites in one go — so the table
also records when each was last checked and what that check found.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from psycopg.rows import DictRow

#: Most companies one account can watch. Every one is a set of requests to
#: someone else's site on each check, and each check is one web request.
MAX_COMPANIES = 40


class CompanyError(ValueError):
    """A company couldn't be added; the message says why, for the user."""


def company_id(careers_url: str) -> str:
    """Short, stable id for a careers URL. Hashed rather than serial, so
    the same URL gets the same id in every account and across re-adds."""
    return hashlib.sha1(careers_url.encode()).hexdigest()[:12]


def _now() -> str:
    # With an explicit offset (unlike the naive utcnow() stamps elsewhere),
    # so the browser's "3 hours ago" doesn't have to guess the timezone.
    # Every stamp shares the format, so TEXT order is time order.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CompanyStore:
    """Watched-company queries; expects ``self.conn`` and ``self.user_id``."""

    def list_companies(self) -> list[DictRow]:
        return self.conn.execute(
            "SELECT * FROM companies WHERE user_id=%s ORDER BY lower(name), added",
            (self.user_id,),
        ).fetchall()

    def get_company(self, cid: str) -> DictRow | None:
        return self.conn.execute(
            "SELECT * FROM companies WHERE user_id=%s AND id=%s", (self.user_id, cid),
        ).fetchone()

    def add_company(self, *, name: str, careers_url: str, ats: str, slug: str) -> DictRow:
        """Insert a company, or raise :class:`CompanyError` if the URL is
        already watched or the list is full.

        The count and the insert run under a per-user transaction lock, so
        two tabs adding at once wait their turn rather than both counting
        39 and both inserting. It's released by the commit (or rollback)."""
        self.conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                          (f"{self.user_id}:companies-add",))
        count = self.conn.execute(
            "SELECT COUNT(*) AS n FROM companies WHERE user_id=%s", (self.user_id,),
        ).fetchone()["n"]
        if count >= MAX_COMPANIES:
            self.conn.rollback()  # releases the lock
            raise CompanyError(
                f"You can watch up to {MAX_COMPANIES} companies. Remove one to add another.")
        row = self.conn.execute(
            """INSERT INTO companies (user_id, id, name, careers_url, ats, slug, added)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING
               RETURNING *""",
            (self.user_id, company_id(careers_url), name, careers_url, ats, slug, _now()),
        ).fetchone()
        self.conn.commit()
        if row is None:
            raise CompanyError("That careers site is already on your list.")
        return row

    def delete_company(self, cid: str) -> bool:
        """Stop watching a company. Postings it already found stay in the
        queue: they're the user's to keep or reject like any other."""
        cur = self.conn.execute(
            "DELETE FROM companies WHERE user_id=%s AND id=%s", (self.user_id, cid),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def next_company_to_check(self, due_before: str | None = None) -> DictRow | None:
        """The company checked longest ago (never-checked first), or None.
        With ``due_before``, only one not checked since that time."""
        query = "SELECT * FROM companies WHERE user_id=%s"
        params: list = [self.user_id]
        if due_before is not None:
            query += " AND (last_checked IS NULL OR last_checked < %s)"
            params.append(due_before)
        query += " ORDER BY last_checked ASC NULLS FIRST, added ASC LIMIT 1"
        return self.conn.execute(query, params).fetchone()

    def count_companies_due(self, due_before: str) -> int:
        """How many companies haven't been checked since ``due_before``."""
        return self.conn.execute(
            """SELECT COUNT(*) AS n FROM companies WHERE user_id=%s
               AND (last_checked IS NULL OR last_checked < %s)""",
            (self.user_id, due_before),
        ).fetchone()["n"]

    def record_company_check(self, cid: str, *, found: int, new: int,
                             error: str | None) -> None:
        """Note a finished check. A failed one counts as checked too: it
        shouldn't jump the queue and be retried on every run."""
        self.conn.execute(
            """UPDATE companies SET last_checked=%s, last_found=%s, last_new=%s, last_error=%s
               WHERE user_id=%s AND id=%s""",
            (_now(), found, new, error, self.user_id, cid),
        )
        self.conn.commit()
