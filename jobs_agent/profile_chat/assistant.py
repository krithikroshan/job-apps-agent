"""Conversational extraction of scoring-profile fields, against the Gemini API.

Turns a free-form description of the job the candidate wants into a proposed
edit of the four Profile fields the Scoring profile tab edits. This module
never saves anything — see the propose/apply split in web/api.py, which
validates and previews whatever gets proposed here before anything is
written to the database.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .. import openrouter
from ..config import gemini_model
from ..profile import Profile
from .prompts import system_instruction

# google-genai logs an "AFC is not recommended" notice on every
# generate_content call even when no tools are used; keep it off the console.
logging.getLogger("google_genai").setLevel(logging.ERROR)

TEMPERATURE = 0.4
MAX_OUTPUT_TOKENS = 1024

WEIGHT_FIELDS = ("target_titles", "domain_terms")
LIST_FIELDS = ("title_blockers", "experience_blockers")
PROPOSAL_FIELDS = WEIGHT_FIELDS + LIST_FIELDS


class ChatError(RuntimeError):
    """Raised when a chat turn can't be completed (missing key, API failure,
    or a model reply that doesn't match the expected shape)."""


def _client():
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ChatError(
            "GEMINI_API_KEY is not set — export it before using the profile assistant."
        )
    try:
        from google import genai
    except ImportError as e:
        raise ChatError("the 'google-genai' package is not installed") from e
    return genai.Client(api_key=api_key)


def _contents(history: list[dict], message: str) -> list[dict]:
    contents = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("content", "")).strip()
        if not text:
            continue
        role = "model" if turn.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": message}]})
    return contents


def _openrouter_messages(system: str, history: list[dict], message: str) -> list[dict]:
    messages = [{"role": "system", "content": system}]
    for turn in history:
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("content", "")).strip()
        if not text:
            continue
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message})
    return messages


def chat_turn(profile: Profile, history: list[dict], message: str) -> dict[str, Any]:
    """One turn of the profile chat.

    ``history`` is the prior turns as ``{"role": "user"|"assistant", "content":
    str}``, oldest first; ``message`` is the new user message, not yet in
    ``history``. Returns ``{"reply": str, "proposal": dict | None}``.
    """
    client = _client()
    from google.genai import types

    system = system_instruction(profile)
    try:
        resp = client.models.generate_content(
            model=gemini_model(),
            contents=_contents(history, message),
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=TEMPERATURE,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
            ),
        )
    except Exception as e:
        if openrouter.is_gemini_overloaded(e):
            try:
                text = openrouter.chat_completion(
                    _openrouter_messages(system, history, message),
                    temperature=TEMPERATURE,
                    max_tokens=MAX_OUTPUT_TOKENS,
                )
            except Exception as fallback_e:
                raise ChatError(
                    f"the profile assistant failed: {e} "
                    f"(OpenRouter fallback also failed: {fallback_e})"
                ) from fallback_e
            return _parse(text)
        raise ChatError(f"the profile assistant failed: {e}") from e

    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise ChatError(f"the model returned no text{_finish_reason(resp)}")
    return _parse(text)


def _parse(text: str) -> dict[str, Any]:
    # Gemini is constrained to raw JSON via response_mime_type, but the
    # OpenRouter fallback isn't, and often wraps its reply in a ```json fence.
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ChatError(f"the model's reply wasn't valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ChatError("the model's reply wasn't a JSON object")

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ChatError("the model's reply had no 'reply' text")

    return {"reply": reply, "proposal": _validate_proposal(data.get("proposal"))}


def _validate_proposal(proposal: Any) -> dict | None:
    if proposal is None:
        return None
    if not isinstance(proposal, dict):
        raise ChatError("the model's proposal wasn't a JSON object")

    unknown = set(proposal) - set(PROPOSAL_FIELDS)
    if unknown:
        raise ChatError(f"the model proposed unknown fields: {', '.join(sorted(unknown))}")

    for field in WEIGHT_FIELDS:
        if field not in proposal:
            continue
        value = proposal[field]
        valid = isinstance(value, dict) and all(
            isinstance(term, str) and isinstance(weight, int) and not isinstance(weight, bool)
            for term, weight in value.items()
        )
        if not valid:
            raise ChatError(f"the model's {field} proposal wasn't a term -> whole number mapping")

    for field in LIST_FIELDS:
        if field not in proposal:
            continue
        value = proposal[field]
        if not (isinstance(value, list) and all(isinstance(term, str) for term in value)):
            raise ChatError(f"the model's {field} proposal wasn't a list of terms")

    return proposal or None


def _finish_reason(resp) -> str:
    """Best-effort ' (finish reason: ...)' suffix for an empty response."""
    try:
        return f" (finish reason: {resp.candidates[0].finish_reason})"
    except (AttributeError, IndexError, TypeError):
        return ""
