"""Fallback completion against OpenRouter, used when Gemini is overloaded.

Gemini occasionally returns a 503 ("model overloaded, try again later")
under load. Rather than failing the request outright, drafter.py and
profile_chat/assistant.py retry once against OpenRouter before giving up —
see :func:`is_gemini_overloaded` and :func:`chat_completion`.

Both fallback models are free-tier: independently rate-limited (per
:func:`openrouter_models`) and backed by their own upstream provider, so a
second model is tried if the first is also saturated rather than treating
one 429 as "OpenRouter is down".
"""

from __future__ import annotations

import os

import httpx

DEFAULT_OPENROUTER_MODELS = (
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3.5-lightning:free",
)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def openrouter_models() -> list[str]:
    """Fallback models tried in order when Gemini is overloaded; override
    with a comma-separated JOBS_AGENT_OPENROUTER_MODELS."""
    override = os.getenv("JOBS_AGENT_OPENROUTER_MODELS")
    if override:
        return [m.strip() for m in override.split(",") if m.strip()]
    return list(DEFAULT_OPENROUTER_MODELS)


def is_gemini_overloaded(exc: Exception) -> bool:
    """True if ``exc`` is Gemini's 503 (model overloaded / unavailable)."""
    try:
        from google.genai import errors
    except ImportError:
        return False
    return isinstance(exc, errors.APIError) and getattr(exc, "code", None) == 503


def chat_completion(messages: list[dict[str, str]], *, temperature: float,
                    max_tokens: int) -> str:
    """One completion from the first fallback model that succeeds.

    ``messages`` is the full turn history, including the leading system
    message, as ``{"role", "content"}`` dicts. Tries each of
    :func:`openrouter_models` in order — each free-tier model has its own
    account and upstream-provider rate limits, so one being saturated
    doesn't mean the others are.

    Raises ``RuntimeError`` (with every model's failure reason) if none of
    them work, or immediately if OPENROUTER_API_KEY is missing; callers wrap
    this in their own error type alongside the Gemini call it's falling
    back for.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    models = openrouter_models()
    failures = []
    for model in models:
        try:
            return _complete(model, messages, api_key, temperature=temperature,
                             max_tokens=max_tokens)
        except RuntimeError as e:
            failures.append(f"{model}: {e}")

    raise RuntimeError("all OpenRouter fallback models failed — " + "; ".join(failures))


def _complete(model: str, messages: list[dict[str, str]], api_key: str, *,
              temperature: float, max_tokens: int) -> str:
    try:
        resp = httpx.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=60,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"{e.response.status_code} {e.response.text}") from e
    except httpx.HTTPError as e:
        raise RuntimeError(str(e)) from e

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected response shape: {data}") from e
