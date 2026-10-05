"""A user's AI settings: which providers to try, in what order, and which
model to ask for at each. Stored as JSON in the ``documents`` table."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..storage import DOC_LLM_SETTINGS, Store
from .registry import DEFAULT_ORDER, PROVIDERS

MAX_MODEL_LENGTH = 120
#: Model ids across providers: letters, digits, . _ - : @, and "/" only
#: between segments (OpenRouter's "vendor/model"). With ".." also refused,
#: no id can walk out of the API path it's placed in.
_MODEL_SHAPE = re.compile(r"^[A-Za-z0-9][\w.:@-]*(?:/[\w.:@-]+)*$")


class SettingsError(ValueError):
    """Settings from the page couldn't be parsed. The message says why."""


@dataclass(frozen=True)
class LLMSettings:
    order: tuple[str, ...] = DEFAULT_ORDER
    #: provider id -> model id; absent means the provider's default.
    models: dict[str, str] = field(default_factory=dict)


def model_for(settings: LLMSettings, provider: str) -> str:
    return settings.models.get(provider) or PROVIDERS[provider].default_model()


def full_order(settings: LLMSettings) -> list[str]:
    """The user's order, then any provider it leaves out — a key the user
    added should still be tried, just last."""
    return list(settings.order) + [p for p in PROVIDERS if p not in settings.order]


def parse_settings(payload: dict) -> LLMSettings:
    order = payload.get("order")
    if not isinstance(order, list) or not all(isinstance(p, str) for p in order):
        raise SettingsError("order: expected a list of provider ids")
    unknown = [p for p in order if p not in PROVIDERS]
    if unknown:
        raise SettingsError(f"order: unknown provider {unknown[0]!r}")
    if len(set(order)) != len(order):
        raise SettingsError("order: each provider can appear once")

    models_in = payload.get("models") or {}
    if not isinstance(models_in, dict):
        raise SettingsError("models: expected provider -> model")
    models: dict[str, str] = {}
    for provider, model in models_in.items():
        if provider not in PROVIDERS:
            raise SettingsError(f"models: unknown provider {provider!r}")
        if not isinstance(model, str):
            raise SettingsError(f"models: {provider} model must be text")
        model = model.strip()
        if not model:
            continue
        if (len(model) > MAX_MODEL_LENGTH or ".." in model
                or not _MODEL_SHAPE.match(model)):
            raise SettingsError(f"models: {model[:40]!r} isn't a model id")
        models[provider] = model
    return LLMSettings(order=tuple(order), models=models)


def load_settings(store: Store) -> LLMSettings:
    """The stored settings, or the defaults if none (or unreadable ones)."""
    raw = store.get_document(DOC_LLM_SETTINGS)
    if not raw.strip():
        return LLMSettings()
    try:
        return parse_settings(json.loads(raw))
    except (json.JSONDecodeError, SettingsError, AttributeError):
        return LLMSettings()


def save_settings(store: Store, settings: LLMSettings) -> None:
    store.set_document(DOC_LLM_SETTINGS, json.dumps(
        {"order": list(settings.order), "models": dict(settings.models)}))
