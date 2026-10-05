"""AI endpoints: analysing postings in batches, profile suggestions, and
plain-English search. Same contract as api.py — plain functions of
``(store, request)``; the AI client comes from :func:`llm.for_user`."""

from __future__ import annotations

from .. import llm
from ..analysis import run_batch, status
from ..analysis.triage import context_hash
from ..llm import LLMError
from ..profile import ProfileError, load_profile
from ..storage import DOC_CV, Store
from ..suggest import SearchError, SuggestError, parse_search, suggest_profile
from ..suggest.profile import HISTORY_TITLES
from .api import Json, Request, error, merge_proposal, profile_as_text


def get_analysis_status(store: Store, req: Request) -> Json:
    s = status(store)
    return Json({"remaining": s.remaining, "analysed": s.analysed, "failed": s.failed})


def post_analyse(store: Store, req: Request) -> Json:
    """One batch, on the user's fast models. The page calls this repeatedly
    until ``remaining`` is 0."""
    with store.exclusive("analyse") as mine:
        if not mine:
            return error("Postings are already being analysed in another tab.", 409)
        try:
            result = run_batch(store, llm.for_user(store, fast=True))
        except LLMError as e:
            return error(str(e))
    return Json({"analysed": result.analysed, "remaining": result.remaining,
                 "failed": result.failed, "model": result.model})


def post_analyse_retry(store: Store, req: Request) -> Json:
    """Put postings the AI gave up on back in the waiting list."""
    store.reset_failed_analyses(context_hash(store))
    return get_analysis_status(store, req)


def post_suggest_profile(store: Store, req: Request) -> Json:
    """A suggested profile edit from the CV (``source: "cv"``) or from what
    was shortlisted and rejected (``"history"``). Returns a preview in the
    same shape as the profile chat; nothing is saved."""
    source = str(req.payload.get("source") or "")
    if source not in ("cv", "history"):
        return error("source must be 'cv' or 'history'")
    profile = load_profile(store)
    try:
        result = suggest_profile(
            llm.for_user(store), profile, source=source,
            cv=store.get_document(DOC_CV),
            kept=store.decided_titles(("shortlisted", "drafted", "approved", "submitted"),
                                      HISTORY_TITLES),
            dropped=store.decided_titles(("rejected",), HISTORY_TITLES),
        )
    except SuggestError as e:
        return error(str(e))
    proposal = result["proposal"]
    if not proposal:
        return Json({"reply": result["reply"], "proposal": None, "preview": None})
    try:
        merged = merge_proposal(profile, proposal)
    except ProfileError as e:
        return error(f"The suggestion couldn't be used: {e}")
    return Json({"reply": result["reply"], "proposal": proposal,
                 "preview": profile_as_text(merged)})


def post_search(store: Store, req: Request) -> Json:
    try:
        return Json(parse_search(llm.for_user(store), str(req.payload.get("query") or "")))
    except SearchError as e:
        return error(str(e))
