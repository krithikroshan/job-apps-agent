"""Cover-letter drafting, against whichever AI provider the user has set up.

For each application the model writes a complete cover letter, tailored to
that posting, in the candidate's own voice. It gets three inputs:

  - the CV: the only source of facts about the candidate; nothing may be
    invented or embellished beyond it;
  - an example letter the candidate wrote themselves: the model matches its
    voice, tone, length, and structure;
  - the job posting: what the letter is tailored to.

This is a real generation step, so every draft is read, edited, and approved
by a human before anything is sent — see the workflow in web/api.py. This
module only produces text. The prompts themselves live in prompts.py, and
provider choice and fallback live in jobs_agent/llm.
"""

from __future__ import annotations

from ..llm import Client, LLMError, Message
from .prompts import DRAFT_SYSTEM, REDRAFT_SYSTEM, draft_prompt, redraft_prompt

TEMPERATURE = 0.7
MAX_OUTPUT_TOKENS = 4096


class DraftError(RuntimeError):
    """Raised when a letter can't be drafted (no provider, API failure, ...)."""


def _generate(client: Client, prompt: str, system: str, *, what: str) -> str:
    try:
        return client.complete(system, [Message("user", prompt)],
                               temperature=TEMPERATURE, max_tokens=MAX_OUTPUT_TOKENS,
                               json_mode=False)
    except LLMError as e:
        raise DraftError(f"{what} failed: {e}") from e


def draft_letter(client: Client, *, title: str, employer: str, location: str,
                 description: str, cv: str, template: str) -> str:
    """Write a full cover letter for one posting, in the candidate's voice.

    ``template`` is the candidate's own example letter, used purely as a
    voice and structure reference — it is not sent as-is.
    """
    prompt = draft_prompt(title=title, employer=employer, location=location,
                          description=description, cv=cv, template=template)
    return _generate(client, prompt, DRAFT_SYSTEM, what="drafting")


def redraft_letter(client: Client, *, title: str, employer: str, location: str,
                   description: str, cv: str, previous_letter: str, feedback: str) -> str:
    """Revise an existing draft to act on the candidate's feedback.

    ``previous_letter`` is the draft being revised (already in the
    candidate's voice); ``feedback`` is what the candidate wants changed
    about it.
    """
    prompt = redraft_prompt(title=title, employer=employer, location=location,
                            description=description, cv=cv,
                            previous_letter=previous_letter, feedback=feedback)
    return _generate(client, prompt, REDRAFT_SYSTEM, what="redrafting")
