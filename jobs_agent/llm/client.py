"""The one object the rest of the app asks for text: tries each of the
user's configured providers in order until one answers."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from ..storage import Store
from . import registry
from .base import LLMError, Message, Prompt
from .keys import SERVER, resolve_key
from .settings import full_order, load_settings, model_for

log = logging.getLogger(__name__)

#: Total time one answer may take across every provider, retry, and
#: fallback — the whole request runs inside a single web request, which a
#: host like Vercel cuts off. Each provider gets whatever is left.
BUDGET_SECONDS = 90.0

NO_PROVIDER = ("No AI provider is set up. Add an API key for Gemini, OpenAI, Claude, or "
               "OpenRouter on the Settings page.")


#: Shown in place of a provider's own error when the operator's key failed:
#: those errors can quote a masked key or account and billing details.
SERVER_KEY_FAILED = "the server's key isn't working (details are in the server log)"


@dataclass(frozen=True)
class Attempt:
    provider: str
    key: str
    model: str
    #: The operator's key, not the user's: errors are redacted for users.
    server_key: bool = False


class Client:
    def __init__(self, attempts: list[Attempt]):
        self.attempts = attempts

    def complete(self, system: str, messages: list[Message], *, temperature: float,
                 max_tokens: int, json_mode: bool = False) -> str:
        if not self.attempts:
            raise LLMError(NO_PROVIDER)
        deadline = time.monotonic() + BUDGET_SECONDS
        prompt = Prompt(system=system, messages=_merge_turns(messages),
                        temperature=temperature, max_tokens=max_tokens,
                        json_mode=json_mode, deadline=deadline)
        failures = []
        for attempt in self.attempts:
            spec = registry.PROVIDERS[attempt.provider]
            if time.monotonic() >= deadline:
                failures.append(f"ran out of time before trying {spec.label}")
                break
            try:
                return spec.complete(attempt.key, attempt.model, prompt)
            except LLMError as e:
                log.warning("%s failed, trying the next provider: %s", spec.label, e)
                failures.append(f"{spec.label}: {SERVER_KEY_FAILED if attempt.server_key else e}")
        raise LLMError("every AI provider failed — " + "; ".join(failures))


def _merge_turns(messages: list[Message]) -> tuple[Message, ...]:
    """Join back-to-back messages from the same role. A failed chat turn
    leaves two user messages in a row, which providers that require
    alternating roles reject."""
    merged: list[Message] = []
    for m in messages:
        if merged and merged[-1].role == m.role:
            merged[-1] = Message(m.role, f"{merged[-1].content}\n\n{m.content}")
        else:
            merged.append(m)
    return tuple(merged)


def for_user(store: Store) -> Client:
    """A client over every provider this user has a key for (theirs or the
    server's), in their preferred order.

    The operator's key always gets the provider's default model: a user may
    choose any model for their own key, but not point the operator's at the
    most expensive one there is."""
    settings = load_settings(store)
    attempts = []
    for provider in full_order(settings):
        resolved = resolve_key(store, provider)
        if not resolved:
            continue
        key, source = resolved
        if source == SERVER:
            model = registry.PROVIDERS[provider].default_model()
        else:
            model = model_for(settings, provider)
        attempts.append(Attempt(provider, key, model, server_key=source == SERVER))
    return Client(attempts)
