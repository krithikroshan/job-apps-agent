"""OpenRouter: one key, many hosted models, including free tiers.
https://openrouter.ai/docs

With no model chosen, it works through :func:`free_models` in order — each
free model is rate-limited independently and served by its own upstream, so
one being saturated doesn't mean the others are.
"""

from __future__ import annotations

import os

from ..base import (
    LLMError,
    Prompt,
    ProviderSpec,
    rejects_param,
    require_text,
    request_json,
    truncated,
)

BASE = "https://openrouter.ai/api/v1"
DEFAULT_FREE_MODELS = (
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3.5-lightning:free",
)


def free_models() -> list[str]:
    """Models tried when none is chosen; override with a comma-separated
    JOBS_AGENT_OPENROUTER_MODELS."""
    override = os.getenv("JOBS_AGENT_OPENROUTER_MODELS")
    if override:
        return [m.strip() for m in override.split(",") if m.strip()]
    return list(DEFAULT_FREE_MODELS)


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def complete(key: str, model: str, prompt: Prompt) -> str:
    failures = []
    for candidate in [model] if model else free_models():
        try:
            return _complete_one(key, candidate, prompt)
        except LLMError as e:
            failures.append(f"{candidate}: {e}")
    raise LLMError("; ".join(failures))


def _complete_one(key: str, model: str, prompt: Prompt) -> str:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": prompt.system}]
                    + [{"role": m.role, "content": m.content} for m in prompt.messages],
        "temperature": prompt.temperature,
        "max_tokens": prompt.max_tokens,
        # The free models are reasoning models that, left on, can spend the
        # whole budget thinking and return the cut-off chain of thought as
        # "content". Only the final text is wanted.
        "reasoning": {"enabled": False},
    }
    url = f"{BASE}/chat/completions"
    try:
        data = request_json("POST", url, headers=_headers(key), json=body,
                            deadline=prompt.deadline)
    except LLMError as e:
        # Some models can't have reasoning switched off, and say so.
        if not rejects_param(e, "reasoning"):
            raise
        body.pop("reasoning")
        data = request_json("POST", url, headers=_headers(key), json=body,
                            deadline=prompt.deadline)
    # An upstream failure mid-request comes back as a 200 with an error body.
    if isinstance(data.get("error"), dict):
        raise LLMError(str(data["error"].get("message") or "upstream error")[:300])
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise truncated()
    return require_text((choice.get("message") or {}).get("content"))


def list_models(key: str) -> list[str]:
    # /models is public, so check the key separately: a bad one must fail here.
    request_json("GET", f"{BASE}/key", headers=_headers(key))
    data = request_json("GET", f"{BASE}/models", headers=_headers(key))
    ids = [m["id"] for m in data.get("data", []) if m.get("id")]
    return sorted(ids, key=lambda i: (not i.endswith(":free"), i))


SPEC = ProviderSpec(
    id="openrouter",
    label="OpenRouter",
    env_vars=("OPENROUTER_API_KEY",),
    default_model=lambda: "",
    fast_model=lambda: "",
    key_url="https://openrouter.ai/settings/keys",
    complete=complete,
    list_models=list_models,
)
