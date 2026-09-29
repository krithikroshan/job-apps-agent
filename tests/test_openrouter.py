"""Multi-model fallback behavior. httpx is mocked out — no network calls."""

import httpx
import pytest
from google.genai import errors

from jobs_agent import openrouter

MESSAGES = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]


def fake_response(status_code, json_body):
    request = httpx.Request("POST", openrouter.OPENROUTER_URL)
    return httpx.Response(status_code, json=json_body, request=request)


def test_the_first_model_succeeding_is_used_as_is(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    seen_models = []

    def fake_post(url, **kwargs):
        seen_models.append(kwargs["json"]["model"])
        return fake_response(200, {"choices": [{"message": {"content": "hello  "}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = openrouter.chat_completion(MESSAGES, temperature=0.5, max_tokens=10)

    assert result == "hello"
    assert seen_models == [openrouter.openrouter_models()[0]]


def test_the_second_model_is_tried_when_the_first_is_rate_limited(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")
    seen_models = []

    def fake_post(url, **kwargs):
        model = kwargs["json"]["model"]
        seen_models.append(model)
        if model == openrouter.openrouter_models()[0]:
            return fake_response(429, {"error": {"message": "rate limited"}})
        return fake_response(200, {"choices": [{"message": {"content": "from second model"}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = openrouter.chat_completion(MESSAGES, temperature=0.5, max_tokens=10)

    assert result == "from second model"
    assert seen_models == list(openrouter.openrouter_models())


def test_all_models_failing_raises_with_every_reason(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")

    def fake_post(url, **kwargs):
        return fake_response(429, {"error": {"message": "rate limited"}})

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(RuntimeError) as exc_info:
        openrouter.chat_completion(MESSAGES, temperature=0.5, max_tokens=10)

    for model in openrouter.openrouter_models():
        assert model in str(exc_info.value)


def test_missing_api_key_is_a_runtime_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        openrouter.chat_completion(MESSAGES, temperature=0.5, max_tokens=10)


def test_the_model_list_is_overridable(monkeypatch):
    monkeypatch.setenv("JOBS_AGENT_OPENROUTER_MODELS", "foo/bar, baz/qux ")
    assert openrouter.openrouter_models() == ["foo/bar", "baz/qux"]


def test_a_gemini_503_is_recognized(monkeypatch):
    assert openrouter.is_gemini_overloaded(
        errors.ServerError(503, {"error": {"message": "overloaded"}})
    )


def test_a_non_503_gemini_error_is_not_recognized(monkeypatch):
    assert not openrouter.is_gemini_overloaded(
        errors.ClientError(400, {"error": {"message": "bad request"}})
    )
    assert not openrouter.is_gemini_overloaded(ValueError("unrelated"))
