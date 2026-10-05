"""Conversational extraction of scoring-profile fields, against whichever AI
provider the user has set up.

Turns a free-form description of the job the candidate wants into a proposed
edit of the four Profile fields the Scoring profile tab edits. This module
never saves anything — see the propose/apply split in web/api.py, which
validates and previews whatever gets proposed here before anything is
written to the database.
"""

from __future__ import annotations

import json
from typing import Any

from ..llm import Client, LLMError, Message
from ..profile import Profile
from .prompts import system_instruction

TEMPERATURE = 0.4
MAX_OUTPUT_TOKENS = 1024

WEIGHT_FIELDS = ("target_titles", "domain_terms")
LIST_FIELDS = ("title_blockers", "experience_blockers")
PROPOSAL_FIELDS = WEIGHT_FIELDS + LIST_FIELDS


class ChatError(RuntimeError):
    """Raised when a chat turn can't be completed (no provider, API failure,
    or a model reply that doesn't match the expected shape)."""


def _messages(history: list[dict], message: str) -> list[Message]:
    out = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("content", "")).strip()
        if not text:
            continue
        role = "assistant" if turn.get("role") == "assistant" else "user"
        out.append(Message(role, text))
    out.append(Message("user", message))
    return out


def chat_turn(client: Client, profile: Profile, history: list[dict],
              message: str) -> dict[str, Any]:
    """One turn of the profile chat.

    ``history`` is the prior turns as ``{"role": "user"|"assistant", "content":
    str}``, oldest first; ``message`` is the new user message, not yet in
    ``history``. Returns ``{"reply": str, "proposal": dict | None}``.
    """
    try:
        text = client.complete(system_instruction(profile), _messages(history, message),
                               temperature=TEMPERATURE, max_tokens=MAX_OUTPUT_TOKENS,
                               json_mode=True)
    except LLMError as e:
        raise ChatError(f"the profile assistant failed: {e}") from e
    return _parse(text)


def _parse(text: str) -> dict[str, Any]:
    # Gemini and OpenAI are constrained to raw JSON, but Claude and the
    # OpenRouter models aren't: they may wrap it in a ```json fence or a
    # sentence of preamble. The object is everything from the first "{" to
    # the last "}".
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
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
