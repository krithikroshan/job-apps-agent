"""Anthropic (Claude models), over the Messages API.
https://docs.anthropic.com/en/api/messages"""

from __future__ import annotations

from ..base import (
    LLMError,
    Prompt,
    ProviderSpec,
    rejects_param,
    require_text,
    request_json,
    truncated,
)

BASE = "https://api.anthropic.com/v1"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"


def _headers(key: str) -> dict[str, str]:
    return {"x-api-key": key, "anthropic-version": API_VERSION}


def complete(key: str, model: str, prompt: Prompt) -> str:
    # No JSON mode: the prompt asks for JSON, and callers strip a ``` fence.
    body = {
        "model": model,
        "system": prompt.system,
        "messages": [{"role": m.role, "content": m.content} for m in prompt.messages],
        "max_tokens": prompt.max_tokens,
        "temperature": prompt.temperature,
    }
    try:
        data = request_json("POST", f"{BASE}/messages", headers=_headers(key), json=body,
                            deadline=prompt.deadline)
    except LLMError as e:
        if not rejects_param(e, "temperature"):
            raise
        body.pop("temperature")
        data = request_json("POST", f"{BASE}/messages", headers=_headers(key), json=body,
                            deadline=prompt.deadline)
    if data.get("stop_reason") == "max_tokens":
        raise truncated()
    return require_text("".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    ))


def list_models(key: str) -> list[str]:
    data = request_json("GET", f"{BASE}/models", headers=_headers(key), params={"limit": 1000})
    return [m["id"] for m in data.get("data", []) if m.get("id")]


SPEC = ProviderSpec(
    id="anthropic",
    label="Claude (Anthropic)",
    env_vars=("ANTHROPIC_API_KEY",),
    default_model=lambda: DEFAULT_MODEL,
    key_url="https://console.anthropic.com/settings/keys",
    complete=complete,
    list_models=list_models,
)
