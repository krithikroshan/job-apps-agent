"""OpenAI (ChatGPT models), over the Chat Completions API.
https://platform.openai.com/docs/api-reference/chat"""

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

BASE = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5-mini"
#: Reasoning models spend completion tokens thinking before they answer, so
#: a budget sized for the answer alone can be used up before it starts.
REASONING_HEADROOM = 4096
#: Model-id fragments that aren't text chat models.
_NOT_CHAT = ("audio", "realtime", "tts", "transcribe", "image", "search", "embedding",
             "moderation", "whisper", "dall-e", "davinci", "babbage")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def complete(key: str, model: str, prompt: Prompt) -> str:
    body = {
        "model": model,
        "messages": [{"role": "system", "content": prompt.system}]
                    + [{"role": m.role, "content": m.content} for m in prompt.messages],
        "max_completion_tokens": prompt.max_tokens + REASONING_HEADROOM,
        "temperature": prompt.temperature,
    }
    if prompt.json_mode:
        body["response_format"] = {"type": "json_object"}
    try:
        data = request_json("POST", f"{BASE}/chat/completions", headers=_headers(key), json=body,
                            deadline=prompt.deadline)
    except LLMError as e:
        if not rejects_param(e, "temperature"):
            raise
        body.pop("temperature")
        data = request_json("POST", f"{BASE}/chat/completions", headers=_headers(key), json=body,
                            deadline=prompt.deadline)
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise truncated()
    return require_text((choice.get("message") or {}).get("content"))


def list_models(key: str) -> list[str]:
    data = request_json("GET", f"{BASE}/models", headers=_headers(key))
    ids = [m.get("id", "") for m in data.get("data", [])]
    return [
        i for i in ids
        if i.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))
        and not any(frag in i for frag in _NOT_CHAT)
    ]


SPEC = ProviderSpec(
    id="openai",
    label="OpenAI",
    env_vars=("OPENAI_API_KEY",),
    default_model=lambda: DEFAULT_MODEL,
    key_url="https://platform.openai.com/api-keys",
    complete=complete,
    list_models=list_models,
)
