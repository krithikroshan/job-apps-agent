"""Cover-letter drafting against the Gemini API.

For each application the model writes a complete cover letter, tailored to
that posting, in the candidate's own voice. It gets three inputs:

  - the CV: the only source of facts about the candidate; nothing may be
    invented or embellished beyond it;
  - an example letter the candidate wrote themselves: the model matches its
    voice, tone, length, and structure;
  - the job posting: what the letter is tailored to.

This is a real generation step, so every draft is read, edited, and approved
by a human before anything is sent — see the workflow in web/api.py. This
module only produces text. The prompts themselves live in prompts.py.
"""

from __future__ import annotations

import logging
import os

from .. import openrouter
from ..config import gemini_model
from .prompts import DRAFT_SYSTEM, REDRAFT_SYSTEM, draft_prompt, redraft_prompt

# google-genai logs an "AFC is not recommended" notice on every
# generate_content call even when no tools are used; keep it off the console.
logging.getLogger("google_genai").setLevel(logging.ERROR)

TEMPERATURE = 0.7
MAX_OUTPUT_TOKENS = 4096


class DraftError(RuntimeError):
    """Raised when a letter can't be drafted (missing key, API failure, ...)."""


def _client():
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise DraftError(
            "GEMINI_API_KEY is not set — export it before drafting letters."
        )
    try:
        from google import genai
    except ImportError as e:
        raise DraftError("the 'google-genai' package is not installed") from e
    return genai.Client(api_key=api_key)


def _generate(prompt: str, system: str, *, what: str) -> str:
    """One generate_content call, with every failure mode as a DraftError.

    Falls back to OpenRouter when Gemini is overloaded (503) rather than
    failing the draft outright.
    """
    client = _client()
    from google.genai import types

    try:
        resp = client.models.generate_content(
            model=gemini_model(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=TEMPERATURE,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )
    except Exception as e:
        if openrouter.is_gemini_overloaded(e):
            try:
                return openrouter.chat_completion(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=TEMPERATURE,
                    max_tokens=MAX_OUTPUT_TOKENS,
                )
            except Exception as fallback_e:
                raise DraftError(f"{what} failed: {e} (OpenRouter fallback also failed: {fallback_e})") from fallback_e
        raise DraftError(f"{what} failed: {e}") from e

    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        raise DraftError(f"the model returned no text{_finish_reason(resp)}")
    return text


def draft_letter(*, title: str, employer: str, location: str, description: str,
                 cv: str, template: str) -> str:
    """Write a full cover letter for one posting, in the candidate's voice.

    ``template`` is the candidate's own example letter, used purely as a
    voice and structure reference — it is not sent as-is.
    """
    prompt = draft_prompt(title=title, employer=employer, location=location,
                          description=description, cv=cv, template=template)
    return _generate(prompt, DRAFT_SYSTEM, what="drafting")


def redraft_letter(*, title: str, employer: str, location: str, description: str,
                   cv: str, previous_letter: str, feedback: str) -> str:
    """Revise an existing draft to act on the candidate's feedback.

    ``previous_letter`` is the draft being revised (already in the
    candidate's voice); ``feedback`` is what the candidate wants changed
    about it.
    """
    prompt = redraft_prompt(title=title, employer=employer, location=location,
                            description=description, cv=cv,
                            previous_letter=previous_letter, feedback=feedback)
    return _generate(prompt, REDRAFT_SYSTEM, what="redrafting")


def _finish_reason(resp) -> str:
    """Best-effort ' (finish reason: ...)' suffix for an empty response."""
    try:
        return f" (finish reason: {resp.candidates[0].finish_reason})"
    except (AttributeError, IndexError, TypeError):
        return ""
