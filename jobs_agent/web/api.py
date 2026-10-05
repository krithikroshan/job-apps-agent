"""API endpoints.

Each endpoint is a plain function of ``(store, request)`` returning a
response object. Nothing here touches sockets or ``BaseHTTPRequestHandler``,
so the approval gate and the validation rules can be tested directly —
handler.py only routes and serialises.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from ..extract import CvExtractError, extract_cv_text
from .. import llm
from ..letters import DraftError, draft_letter, redraft_letter
from ..pipeline import fetch_and_store_sync
from ..presets import PRESETS, get_preset
from ..profile import (
    DEFAULT_PROFILE,
    MAX_DOMAIN_ONLY_THRESHOLD,
    MAX_RADIUS_MILES,
    ProfileError,
    format_lines,
    format_locations,
    format_salary_bands,
    format_weights,
    load_profile,
    parse_int,
    parse_lines,
    parse_locations,
    parse_salary_bands,
    parse_weights,
    save_profile,
)
from ..profile_chat import ChatError, chat_turn
from ..sources import NoSourcesConfigured
from ..storage.analyses import AIFilters
from ..storage import (
    DOC_CANDIDATE_NAME,
    DOC_CV,
    DOC_CV_FILENAME,
    DOC_TEMPLATE,
    Store,
)

# Full application lifecycle. "approved" requires a non-empty letter;
# "submitted" requires the posting to already be "approved" — enforced here,
# not just hidden in the UI.
UI_STATUSES = ("new", "shortlisted", "drafted", "approved", "submitted", "rejected")

CV_EXTENSIONS = (".docx", ".pdf")

#: Document ids the Profile page's "Save profile" button may write.
EDITABLE_DOC_IDS = (DOC_TEMPLATE, DOC_CANDIDATE_NAME)

_CV_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass
class Request:
    """What an endpoint needs from the HTTP layer."""

    query: dict[str, list[str]] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    def param(self, name: str, default: str = "") -> str:
        return self.query.get(name, [default])[0]

    def int_param(self, name: str, default: int) -> int:
        try:
            return int(self.param(name, str(default)))
        except ValueError:
            return default

    def float_param(self, name: str) -> float | None:
        raw = self.param(name).strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None


@dataclass
class Json:
    body: Any
    status: int = 200


@dataclass
class File:
    data: bytes
    content_type: str
    filename: str


def error(message: str, status: int = 400) -> Json:
    return Json({"error": message}, status)


def _row_to_dict(row) -> dict:
    return {k: row[k] for k in row.keys() if k != "user_id"}


# -- GET ------------------------------------------------------------------

def get_stats(store: Store, req: Request) -> Json:
    return Json(store.stats())


def get_queue(store: Store, req: Request) -> Json:
    rows = store.queue(
        status=req.param("status", "new"),
        min_score=req.int_param("min_score", 0),
        limit=req.int_param("limit", 50),
        location=req.param("location").strip() or None,
        min_salary=req.float_param("min_salary"),
        max_salary=req.float_param("max_salary"),
        contract_type=req.param("contract_type").strip() or None,
        ai=ai_filters(req),
    )
    return Json([_row_to_dict(r) for r in rows])


def ai_filters(req: Request) -> AIFilters:
    """The analysis-based filters from the query string; values outside
    the known set are ignored rather than refused."""
    visa = req.param("visa")
    level = req.param("level")
    years = req.param("max_years").strip()
    flag = lambda name: req.param(name) in ("1", "true")  # noqa: E731
    return AIFilters(
        visa=visa if visa in ("offered", "hide_not_offered") else None,
        level=level if level in ("entry", "junior") else None,
        max_years=int(years) if years.isascii() and years.isdigit() and int(years) <= 15 else None,
        graduate_scheme=flag("graduate_scheme"),
        study_support=flag("study_support"),
        hide_red_flags=flag("hide_red_flags"),
    )


def get_documents(store: Store, req: Request) -> Json:
    return Json({
        "candidate_name": store.get_document(DOC_CANDIDATE_NAME),
        "cv": store.get_document(DOC_CV),
        "cv_filename": store.get_document(DOC_CV_FILENAME),
        "cover_letter_template": store.get_document(DOC_TEMPLATE),
    })


def get_cv_file(store: Store, req: Request) -> File | Json:
    row = store.get_file(DOC_CV)
    if not row:
        return error("no CV on file", 404)
    ext = row["filename"].lower().rsplit(".", 1)[-1]
    return File(
        data=row["data"],
        content_type=_CV_CONTENT_TYPES.get(ext, "application/octet-stream"),
        filename=row["filename"],
    )


def get_profile(store: Store, req: Request) -> Json:
    return Json(profile_as_text(load_profile(store)))


def get_presets(store: Store, req: Request) -> Json:
    """Every preset, in the same text form ``get_profile`` returns, so the
    Profile page can load one into its editor for review before saving."""
    return Json([
        {"id": p.id, "label": p.label, "description": p.description,
         "profile": profile_as_text(p.profile)}
        for p in PRESETS
    ])


# -- POST -----------------------------------------------------------------

def post_fetch(store: Store, req: Request) -> Json:
    per_keyword = int(req.payload.get("per_keyword", 100))
    try:
        result = fetch_and_store_sync(store, per_keyword=per_keyword)
    except NoSourcesConfigured as e:
        return error(str(e))
    return Json(result.as_dict())


def post_status(store: Store, req: Request) -> Json:
    key, status = req.payload.get("key"), req.payload.get("status")
    if not key or status not in UI_STATUSES:
        return error("bad key or status")

    app_row = store.get_application(key)
    if not app_row:
        return error("unknown posting", 404)
    if status == "approved" and not (app_row["letter"] or "").strip():
        return error("Prepare the application (draft a letter) before approving.")
    if status == "submitted" and app_row["status"] != "approved":
        return error("Approve the application before marking it submitted.")

    store.set_status(key, status)
    return Json({"ok": True})


def post_delete(store: Store, req: Request) -> Json:
    key = req.payload.get("key")
    if not key:
        return error("missing key")
    if not store.delete_posting(key):
        return error("unknown posting", 404)
    return Json({"ok": True})


def post_draft(store: Store, req: Request) -> Json:
    key = req.payload.get("key")
    if not key:
        return error("missing key")

    posting = store.get_posting(key)
    if not posting:
        return error("unknown posting", 404)

    cv = store.get_document(DOC_CV)
    template = store.get_document(DOC_TEMPLATE)
    if not cv.strip() or not template.strip():
        return error("Add your CV and an example cover letter on the "
                     "Profile page first.")

    try:
        letter = draft_letter(
            llm.for_user(store),
            title=posting["title"], employer=posting["employer"],
            location=posting["location"], description=posting["description"],
            cv=cv, template=template,
        )
    except DraftError as e:
        return error(str(e))

    store.set_letter(key, letter)
    store.set_status(key, "drafted")
    return Json({"ok": True, "letter": letter})


def post_redraft(store: Store, req: Request) -> Json:
    key = req.payload.get("key")
    feedback = (req.payload.get("feedback") or "").strip()
    if not key or not feedback:
        return error("missing key or feedback")

    posting = store.get_posting(key)
    app_row = store.get_application(key)
    if not posting or not app_row:
        return error("unknown posting", 404)

    # The browser sends the textarea's current contents, so unsaved edits are
    # what gets revised; fall back to the stored draft when it doesn't.
    previous_letter = req.payload.get("letter")
    if previous_letter is None:
        previous_letter = app_row["letter"] or ""
    if not previous_letter.strip():
        return error("Prepare the application (draft a letter) before "
                     "redrafting with feedback.")

    cv = store.get_document(DOC_CV)
    if not cv.strip():
        return error("Add your CV on the Profile page first.")

    try:
        letter = redraft_letter(
            llm.for_user(store),
            title=posting["title"], employer=posting["employer"],
            location=posting["location"], description=posting["description"],
            cv=cv, previous_letter=previous_letter, feedback=feedback,
        )
    except DraftError as e:
        return error(str(e))

    store.set_letter(key, letter)
    return Json({"ok": True, "letter": letter})


def post_letter(store: Store, req: Request) -> Json:
    key, letter = req.payload.get("key"), req.payload.get("letter")
    if not key or letter is None:
        return error("missing key or letter")
    if not store.get_application(key):
        return error("unknown posting", 404)
    store.set_letter(key, letter)
    return Json({"ok": True})


def post_cv(store: Store, req: Request) -> Json:
    filename = (req.payload.get("filename") or "cv").strip()
    if not filename.lower().endswith(CV_EXTENSIONS):
        return error("Upload a .docx or .pdf file.")
    try:
        blob = base64.b64decode(req.payload.get("data_b64") or "", validate=True)
    except (ValueError, TypeError):
        return error("could not decode the upload")
    try:
        text = extract_cv_text(filename, blob)
    except CvExtractError as e:
        return error(f"Could not read that file: {e}")
    if not text.strip():
        return error("No readable text found — if this is a scanned or "
                     "image-only CV, upload a text-based version.")

    store.set_file(DOC_CV, filename, blob)     # the original document
    store.set_document(DOC_CV, text)           # cached extracted text
    store.set_document(DOC_CV_FILENAME, filename)
    return Json({"ok": True, "filename": filename,
                 "chars": len(text), "text": text})


def post_documents(store: Store, req: Request) -> Json:
    for doc_id in EDITABLE_DOC_IDS:
        if doc_id in req.payload:
            store.set_document(doc_id, req.payload[doc_id])
    return Json({"ok": True})


def post_profile(store: Store, req: Request) -> Json:
    """Save the edited scoring profile.

    Nothing is written unless all four sections parse, so a typo in the last
    one can't leave a half-applied profile behind.
    """
    current = load_profile(store)
    try:
        updated = _profile_from_text(req.payload, base=current)
    except ProfileError as e:
        return error(str(e))
    if not updated.target_titles:
        return error("Target titles can't be empty — a posting matching none "
                     "of them is dropped, so an empty list drops everything.")
    save_profile(store, updated)
    return Json({"ok": True, "profile": profile_as_text(updated)})


def post_profile_reset(store: Store, req: Request) -> Json:
    save_profile(store, DEFAULT_PROFILE)
    return Json({"ok": True, "profile": profile_as_text(DEFAULT_PROFILE)})


def post_profile_chat(store: Store, req: Request) -> Json:
    """One turn of the scoring-profile chat assistant.

    This never saves anything: it returns a proposed profile for the Scoring
    profile tab to preview, and the candidate applies it (or not) through the
    existing ``post_profile`` path — same validation, same save.
    """
    message = (req.payload.get("message") or "").strip()
    if not message:
        return error("missing message")

    current = load_profile(store)
    pending = req.payload.get("pending_proposal")
    try:
        draft = merge_proposal(current, pending) if isinstance(pending, dict) else current
    except ProfileError:
        draft = current  # a stale/bad pending proposal — fall back to what's saved

    try:
        result = chat_turn(llm.for_user(store), draft,
                           req.payload.get("history") or [], message)
    except ChatError as e:
        return error(str(e))

    proposal = result["proposal"]
    if not proposal:
        return Json({"reply": result["reply"], "proposal": None, "preview": None})

    try:
        merged = merge_proposal(draft, proposal)
    except ProfileError as e:
        return Json({
            "reply": f"{result['reply']} (I couldn't apply that: {e})",
            "proposal": None,
            "preview": None,
        })
    return Json({
        "reply": result["reply"],
        "proposal": proposal,
        "preview": profile_as_text(merged),
    })


# -- profile text round-trip ----------------------------------------------

def profile_as_text(profile) -> dict[str, str]:
    return {
        "target_titles": format_weights(profile.target_titles),
        "domain_terms": format_weights(profile.domain_terms),
        "title_blockers": format_lines(profile.title_blockers),
        "experience_blockers": format_lines(profile.experience_blockers),
        "locations": format_locations(profile.locations),
        "radius_miles": str(profile.radius_miles),
        "salary_bands": format_salary_bands(profile.salary_bands),
        "contract_bonus": str(profile.contract_bonus),
        "domain_only_threshold": str(profile.domain_only_threshold),
        "preset": profile.preset,
    }


def _profile_from_text(payload: dict, *, base):
    """A copy of ``base`` with whichever sections the payload supplies.

    ``name`` is carried over — it isn't edited here.
    """
    from dataclasses import replace

    changes: dict[str, Any] = _search_settings_from_text(payload)
    if "target_titles" in payload:
        changes["target_titles"] = parse_weights(
            payload["target_titles"], what="Target titles")
    if "domain_terms" in payload:
        changes["domain_terms"] = parse_weights(
            payload["domain_terms"], what="Domain terms")
    if "title_blockers" in payload:
        changes["title_blockers"] = parse_lines(payload["title_blockers"])
    if "experience_blockers" in payload:
        changes["experience_blockers"] = parse_lines(payload["experience_blockers"])
    return replace(base, **changes)


def _search_settings_from_text(payload: dict) -> dict[str, Any]:
    """The search-area and scoring-knob fields the payload supplies, parsed
    and range-checked."""
    for key in ("locations", "radius_miles", "salary_bands", "contract_bonus",
                "domain_only_threshold", "preset"):
        if key in payload and not isinstance(payload[key], (str, type(None))):
            raise ProfileError(f"{key}: expected text")
    changes: dict[str, Any] = {}
    if "locations" in payload:
        changes["locations"] = parse_locations(payload["locations"])
    if "radius_miles" in payload:
        changes["radius_miles"] = parse_int(
            payload["radius_miles"], what="Radius", lo=0, hi=MAX_RADIUS_MILES)
    if "salary_bands" in payload:
        changes["salary_bands"] = parse_salary_bands(payload["salary_bands"])
    if "contract_bonus" in payload:
        changes["contract_bonus"] = parse_int(
            payload["contract_bonus"], what="Contract bonus", lo=-50, hi=50)
    if "domain_only_threshold" in payload:
        changes["domain_only_threshold"] = parse_int(
            payload["domain_only_threshold"], what="Domain-only threshold",
            lo=0, hi=MAX_DOMAIN_ONLY_THRESHOLD)
    if "preset" in payload:
        # Display-only: an id from a since-removed preset is dropped rather
        # than refused, or a profile saved with it could never be saved again.
        preset = payload["preset"] or ""
        changes["preset"] = preset if get_preset(preset) else ""
    return changes


def merge_proposal(base, proposal: dict):
    """``base`` with the chat assistant's proposed fields substituted in, via
    the same text round-trip (and so the same validation) manual edits go
    through — a proposal is just another partial, full-value update."""
    payload: dict[str, str] = {}
    for field in ("target_titles", "domain_terms"):
        if field in proposal:
            value = proposal[field]
            if not isinstance(value, dict):
                raise ProfileError(f"{field}: expected a term -> weight mapping")
            payload[field] = format_weights(value)
    for field in ("title_blockers", "experience_blockers"):
        if field in proposal:
            value = proposal[field]
            if not isinstance(value, list):
                raise ProfileError(f"{field}: expected a list of terms")
            payload[field] = format_lines(value)
    return _profile_from_text(payload, base=base)

