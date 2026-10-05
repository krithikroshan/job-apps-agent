"""Storage for AI analyses of postings, and the Match score that blends
them into ranking. Mixed into :class:`~jobs_agent.storage.store.Store`, so
every query is scoped to the store's user like the rest."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterator

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

#: Application statuses still worth analysing: not set aside, not sent.
ACTIVE_STATUSES = ("new", "shortlisted", "drafted", "approved")
#: Keyword scores above this all count as a perfect keyword match.
KEYWORD_CEILING = 80


def match_score(score: int, fit: int | None) -> int:
    """0-100: the keyword score rescaled, blended 40/60 with the AI's fit
    when there is one. Integer arithmetic, so Python and SQL round alike:
    with s the clamped keyword score, 0.4 * (s * 100/80) is exactly s / 2."""
    s = max(0, min(score, KEYWORD_CEILING))
    if fit is None:
        return (5 * s + 2) // 4
    return (5 * s + 6 * fit + 5) // 10


#: The same formula in SQL, over the queue query's ``p`` and ``x`` aliases.
MATCH_SQL = """(CASE WHEN (x.result->>'fit') IS NULL
      THEN (5 * LEAST(GREATEST(p.score, 0), 80) + 2) / 4
      ELSE (5 * LEAST(GREATEST(p.score, 0), 80) + 6 * (x.result->>'fit')::int + 5) / 10
    END)"""


@dataclass(frozen=True)
class AIFilters:
    """Queue filters that read the analysis. ``None``/``False`` is off.
    Unanalysed postings pass every filter that hides something (they might
    qualify) and fail every filter that requires something."""
    visa: str | None = None          # "offered" | "hide_not_offered"
    level: str | None = None         # "entry" | "junior"
    max_years: int | None = None
    graduate_scheme: bool = False
    study_support: bool = False
    hide_red_flags: bool = False

    def sql(self) -> tuple[str, list]:
        clauses, params = [], []
        if self.visa == "offered":
            clauses.append("x.result->>'visa' = 'offered'")
        elif self.visa == "hide_not_offered":
            clauses.append("COALESCE(x.result->>'visa', '') <> 'not_offered'")
        levels = {"entry": ["graduate", "entry", "unknown"],
                  "junior": ["graduate", "entry", "junior", "unknown"]}.get(self.level or "")
        if levels:
            clauses.append("COALESCE(x.result->>'seniority', 'unknown') = ANY(%s)")
            params.append(levels)
        if self.max_years is not None:
            clauses.append("COALESCE((x.result->>'min_years')::int, 0) <= %s")
            params.append(self.max_years)
        if self.graduate_scheme:
            clauses.append("(x.result->>'graduate_scheme')::boolean IS TRUE")
        if self.study_support:
            clauses.append("(x.result->>'study_support')::boolean IS TRUE")
        if self.hide_red_flags:
            clauses.append("(CASE WHEN jsonb_typeof(x.result->'red_flags') = 'array' "
                           "THEN jsonb_array_length(x.result->'red_flags') ELSE 0 END) = 0")
        return "".join(f" AND {c}" for c in clauses), params


class AnalysisStore:
    """Analysis queries; expects ``self.conn`` and ``self.user_id``."""

    @contextmanager
    def exclusive(self, name: str) -> Iterator[bool]:
        """Yields whether this user's ``name`` job could be claimed: False
        if it's already running, here or on another connection (another
        tab, another serverless instance). Postgres advisory locks are
        re-entrant within a session, so same-store nesting is tracked too."""
        held = self.__dict__.setdefault("_exclusive", set())
        if name in held:
            yield False
            return
        lock = "SELECT pg_try_advisory_lock(hashtext(%s)) AS ok"
        got = self.conn.execute(lock, (f"{self.user_id}:{name}",)).fetchone()["ok"]
        self.conn.commit()
        if not got:
            yield False
            return
        held.add(name)
        try:
            yield True
        finally:
            held.discard(name)
            self.conn.execute("SELECT pg_advisory_unlock(hashtext(%s))",
                              (f"{self.user_id}:{name}",))
            self.conn.commit()

    def take_ai_call(self, daily_cap: int) -> bool:
        """Count one server-key AI call for today, unless the cap is
        already reached. Atomic, so parallel requests can't overshoot."""
        day = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            """INSERT INTO ai_usage (user_id, day, calls) VALUES (%s, %s, 1)
               ON CONFLICT (user_id, day) DO UPDATE SET calls = ai_usage.calls + 1
                 WHERE ai_usage.calls < %s
               RETURNING calls""",
            (self.user_id, day, daily_cap),
        ).fetchone()
        self.conn.commit()
        return row is not None and row["calls"] <= daily_cap

    def get_analysis(self, key: str) -> DictRow | None:
        return self.conn.execute(
            "SELECT * FROM posting_analysis WHERE user_id=%s AND posting_key=%s",
            (self.user_id, key),
        ).fetchone()

    def save_analysis(self, key: str, context: str, status: str, result: dict | None,
                      model: str | None, error: str | None) -> None:
        """Record an attempt. Attempts count up while the context stays the
        same; a failure keeps whatever result was there before."""
        self.conn.execute(
            """INSERT INTO posting_analysis
                 (user_id, posting_key, context, status, result, model, error, attempts, updated)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s)
               ON CONFLICT (user_id, posting_key) DO UPDATE SET
                 status = excluded.status,
                 result = COALESCE(excluded.result, posting_analysis.result),
                 model = COALESCE(excluded.model, posting_analysis.model),
                 error = excluded.error,
                 attempts = CASE WHEN posting_analysis.context = excluded.context
                                 THEN posting_analysis.attempts + 1 ELSE 1 END,
                 context = excluded.context,
                 updated = excluded.updated""",
            (self.user_id, key, context, status,
             Jsonb(result) if result is not None else None, model, error,
             datetime.utcnow().isoformat()),
        )
        self.conn.commit()

    def reset_failed_analyses(self, context: str) -> None:
        """Give postings the model failed on, under this context, another go."""
        self.conn.execute(
            "UPDATE posting_analysis SET attempts = 0 "
            "WHERE user_id = %s AND context = %s AND status = 'failed'",
            (self.user_id, context),
        )
        self.conn.commit()

    def analysis_candidates(self, top_n: int) -> list[DictRow]:
        """The ``top_n`` active postings by keyword score, each with its
        analysis row's status, context and attempts (NULL if none)."""
        return self.conn.execute(
            """SELECT p.key, p.title, p.employer, p.location, p.description,
                       p.salary_min, p.salary_max, p.contract_type,
                       x.status AS analysis_status, x.context, x.attempts
                FROM postings p
                JOIN applications a ON a.user_id = p.user_id AND a.posting_key = p.key
                LEFT JOIN posting_analysis x ON x.user_id = p.user_id AND x.posting_key = p.key
                WHERE p.user_id = %s AND a.status = ANY(%s)
                ORDER BY p.score DESC, p.first_seen DESC
                LIMIT %s""",
            (self.user_id, list(ACTIVE_STATUSES), top_n),
        ).fetchall()

    def decided_titles(self, statuses: tuple[str, ...], limit: int) -> list[str]:
        """Titles of postings in ``statuses``, most recently decided first."""
        rows = self.conn.execute(
            """SELECT p.title FROM postings p
               JOIN applications a ON a.user_id = p.user_id AND a.posting_key = p.key
               WHERE p.user_id = %s AND a.status = ANY(%s)
               ORDER BY a.updated DESC LIMIT %s""",
            (self.user_id, list(statuses), limit),
        ).fetchall()
        return [r["title"] for r in rows]
