"""Every supported provider, by id. Adding one means a module in
``providers/`` and an entry here."""

from __future__ import annotations

from .base import ProviderSpec
from .providers import anthropic, gemini, openai, openrouter

PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec for spec in (gemini.SPEC, openai.SPEC, anthropic.SPEC, openrouter.SPEC)
}

#: Fallback order for a user who hasn't chosen one: Gemini then OpenRouter
#: is how drafting worked before multiple providers were supported.
DEFAULT_ORDER: tuple[str, ...] = ("gemini", "openrouter", "anthropic", "openai")
