"""Analysing postings in small batches.

Each call to :func:`run_batch` sends up to ``BATCH_SIZE`` postings to the
user's fast AI model in one request and stores what comes back. The queue
page calls it repeatedly (one web request per batch) until nothing is left,
which keeps every request short enough for a serverless host.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from dataclasses import dataclass

from ..llm import Client, Message
from ..profile import load_profile
from ..storage import DOC_CV, Store
from .prompts import PROMPT_VERSION, SYSTEM, batch_prompt, candidate_block, posting_block
from .schema import AnalysisError, parse_batch

log = logging.getLogger(__name__)

#: Small enough that a slow model answers well inside the client's time budget.
BATCH_SIZE = 6
#: Only the best postings by keyword score are worth a model's time; the
#: long tail is mostly noise and would cost the most.
MAX_ANALYSED = 150
#: A posting the model keeps skipping or garbling is given up on after this
#: many tries with the same CV and profile.
MAX_ATTEMPTS = 2
TEMPERATURE = 0.2
#: Generous: six structured answers plus room for a reasoning model.
MAX_OUTPUT_TOKENS = 6000


@dataclass(frozen=True)
class Status:
    remaining: int
    analysed: int
    failed: int


@dataclass(frozen=True)
class BatchResult:
    analysed: int
    remaining: int
    failed: int
    model: str | None


def context_hash(store: Store) -> str:
    """Everything an analysis depends on besides the posting itself: the
    prompt, the CV, and the parts of the profile the prompt includes. Other
    profile edits (salary bands, blockers) don't change what the AI would
    say, so they don't trigger a costly re-analysis."""
    profile = load_profile(store)
    parts = [str(PROMPT_VERSION), store.get_document(DOC_CV),
             "|".join(profile.target_titles), "|".join(profile.locations)]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def _classify(rows, context: str) -> tuple[list, int, int]:
    pending, analysed, failed = [], 0, 0
    for row in rows:
        current = row["context"] == context
        if current and row["analysis_status"] == "done":
            analysed += 1
        elif current and row["attempts"] >= MAX_ATTEMPTS:
            failed += 1
        else:
            pending.append(row)
    return pending, analysed, failed


_WORDS = re.compile(r"[a-z]{3,}")
#: Share of a visa quote's words that must appear in the posting itself.
EVIDENCE_OVERLAP = 0.8


def _check_visa(result: dict, description: str) -> dict:
    """Keep a visa claim only if its quote is really in the posting. A
    model can be talked into inventing one (by this posting or another in
    the batch), and the page shows it as the employer's own words."""
    if result["visa"] == "not_mentioned":
        return result
    quoted = _WORDS.findall((result["visa_evidence"] or "").lower())
    present = set(_WORDS.findall(description.lower()))
    if quoted and sum(w in present for w in quoted) / len(quoted) >= EVIDENCE_OVERLAP:
        return result
    return {**result, "visa": "not_mentioned", "visa_evidence": None}


def status(store: Store) -> Status:
    pending, analysed, failed = _classify(store.analysis_candidates(MAX_ANALYSED),
                                          context_hash(store))
    return Status(remaining=len(pending), analysed=analysed, failed=failed)


def run_batch(store: Store, client: Client) -> BatchResult:
    """Analyse the next batch. Provider failures raise
    :class:`~jobs_agent.llm.LLMError` with nothing saved; postings the model
    skipped or garbled count an attempt and are retried next time."""
    context = context_hash(store)
    pending, analysed, failed = _classify(store.analysis_candidates(MAX_ANALYSED), context)
    batch = pending[:BATCH_SIZE]
    if not batch:
        return BatchResult(analysed=0, remaining=0, failed=failed, model=None)

    profile = load_profile(store)
    candidate = candidate_block(cv=store.get_document(DOC_CV),
                                target_titles=list(profile.target_titles),
                                locations=profile.locations)
    ids = {i: row["key"] for i, row in enumerate(batch, start=1)}
    fence = secrets.token_hex(6)   # unguessable, so a posting can't fake its end
    prompt = batch_prompt(candidate, [posting_block(i, row, fence)
                                      for i, row in enumerate(batch, 1)])

    text, model = client.complete_with_source(
        SYSTEM, [Message("user", prompt)], temperature=TEMPERATURE,
        max_tokens=MAX_OUTPUT_TOKENS, json_mode=True)
    try:
        results = parse_batch(text, ids)
    except AnalysisError as e:
        log.warning("unreadable analysis batch from %s: %s", model, e)
        results = {}
    if len(results) < len(ids):
        # Lengths only: the reply can quote the CV.
        log.warning("%s analysed %d of %d postings (reply was %d characters)",
                    model, len(results), len(ids), len(text))
    descriptions = {row["key"]: row["description"] or "" for row in batch}
    results = {k: _check_visa(r, descriptions[k]) for k, r in results.items()}

    for key in ids.values():
        if key in results:
            store.save_analysis(key, context, "done", results[key], model, None)
        else:
            store.save_analysis(key, context, "failed", None, model,
                                "the model didn't return an analysis for this posting")
    done_now = sum(1 for k in ids.values() if k in results)
    after = status(store)
    return BatchResult(analysed=done_now, remaining=after.remaining, failed=after.failed,
                       model=model)
