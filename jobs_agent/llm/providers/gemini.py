"""Google Gemini, over its REST API. https://ai.google.dev/api"""

from __future__ import annotations

from urllib.parse import quote

from ...config import gemini_model
from ..base import Prompt, ProviderSpec, require_text, request_json, truncated

BASE = "https://generativelanguage.googleapis.com/v1beta"


def _headers(key: str) -> dict[str, str]:
    return {"x-goog-api-key": key}


def complete(key: str, model: str, prompt: Prompt) -> str:
    config = {"temperature": prompt.temperature, "maxOutputTokens": prompt.max_tokens}
    if prompt.json_mode:
        config["responseMimeType"] = "application/json"
    # The model is a path segment; quoting keeps any "/" or "." inside it.
    data = request_json("POST", f"{BASE}/models/{quote(model, safe='')}:generateContent",
                        headers=_headers(key), json={
        "systemInstruction": {"parts": [{"text": prompt.system}]},
        "contents": [
            {"role": "model" if m.role == "assistant" else "user",
             "parts": [{"text": m.content}]}
            for m in prompt.messages
        ],
        "generationConfig": config,
    }, deadline=prompt.deadline)
    candidate = (data.get("candidates") or [{}])[0]
    finish = candidate.get("finishReason")
    if finish == "MAX_TOKENS":
        raise truncated()
    # Thinking models return their reasoning as parts flagged "thought".
    parts = (candidate.get("content") or {}).get("parts") or []
    blocked = (data.get("promptFeedback") or {}).get("blockReason")
    return require_text("".join(p.get("text", "") for p in parts if not p.get("thought")),
                        why=f"blocked: {blocked}" if blocked else finish or "")


def list_models(key: str) -> list[str]:
    data = request_json("GET", f"{BASE}/models", headers=_headers(key),
                        params={"pageSize": 1000})
    return [
        m["name"].removeprefix("models/")
        for m in data.get("models", [])
        if m.get("name") and "generateContent" in m.get("supportedGenerationMethods", [])
    ]


SPEC = ProviderSpec(
    id="gemini",
    label="Gemini",
    env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    default_model=gemini_model,
    key_url="https://aistudio.google.com/apikey",
    complete=complete,
    list_models=list_models,
)
