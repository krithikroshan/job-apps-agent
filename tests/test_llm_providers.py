"""Provider adapters: request shape and response parsing. Every HTTP call
goes through a MockTransport — nothing touches the network."""

import json

import httpx
import pytest

from jobs_agent.llm import base
from jobs_agent.llm.base import LLMError, Message, Prompt
from jobs_agent.llm.registry import PROVIDERS

PROMPT = Prompt(
    system="be brief",
    messages=(Message("user", "hi"), Message("assistant", "hello"), Message("user", "again")),
    temperature=0.5,
    max_tokens=100,
)


@pytest.fixture
def http(monkeypatch):
    """Queue responses; record requests."""
    state = {"responses": [], "requests": []}

    def handler(request):
        state["requests"].append(request)
        status, body = state["responses"].pop(0)
        return httpx.Response(status, json=body)

    monkeypatch.setattr(base, "TRANSPORT", httpx.MockTransport(handler))
    return state


def body_of(request):
    return json.loads(request.content)


# -- gemini ----------------------------------------------------------------

def test_gemini_request_and_reply(http):
    http["responses"].append((200, {"candidates": [{
        "content": {"parts": [{"text": "thinking", "thought": True}, {"text": " answer "}]},
        "finishReason": "STOP"}]}))
    out = PROVIDERS["gemini"].complete("gk", "gemini-x", PROMPT)
    assert out == "answer"
    req = http["requests"][0]
    assert req.url.path.endswith("/models/gemini-x:generateContent")
    assert req.headers["x-goog-api-key"] == "gk"
    body = body_of(req)
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"]["maxOutputTokens"] == 100


def test_gemini_json_mode(http):
    http["responses"].append((200, {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}))
    PROVIDERS["gemini"].complete("gk", "m", Prompt(**{**PROMPT.__dict__, "json_mode": True}))
    assert body_of(http["requests"][0])["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_truncated_reply_is_an_error(http):
    http["responses"].append((200, {"candidates": [{
        "content": {"parts": [{"text": "half a lett"}]}, "finishReason": "MAX_TOKENS"}]}))
    with pytest.raises(LLMError, match="cut off"):
        PROVIDERS["gemini"].complete("gk", "m", PROMPT)


def test_gemini_lists_generation_models(http):
    http["responses"].append((200, {"models": [
        {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
    ]}))
    assert PROVIDERS["gemini"].list_models("gk") == ["gemini-a"]


# -- openai ----------------------------------------------------------------

def test_openai_request_and_reply(http):
    http["responses"].append((200, {"choices": [
        {"message": {"content": "answer"}, "finish_reason": "stop"}]}))
    out = PROVIDERS["openai"].complete("ok", "gpt-x", PROMPT)
    assert out == "answer"
    req = http["requests"][0]
    assert req.headers["authorization"] == "Bearer ok"
    body = body_of(req)
    assert body["model"] == "gpt-x"
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert [m["role"] for m in body["messages"][1:]] == ["user", "assistant", "user"]
    assert "max_completion_tokens" in body


def test_openai_retries_without_temperature_when_the_model_rejects_it(http):
    """Reasoning models only accept the default temperature."""
    http["responses"] += [
        (400, {"error": {"message": "Unsupported value: 'temperature' does not support 0.5"}}),
        (200, {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}),
    ]
    assert PROVIDERS["openai"].complete("ok", "o-x", PROMPT) == "answer"
    assert "temperature" in body_of(http["requests"][0])
    assert "temperature" not in body_of(http["requests"][1])


def test_openai_lists_chat_models_only(http):
    http["responses"].append((200, {"data": [
        {"id": "gpt-x"}, {"id": "text-embedding-3"}, {"id": "gpt-x-realtime"}, {"id": "o4-mini"}]}))
    assert PROVIDERS["openai"].list_models("ok") == ["gpt-x", "o4-mini"]


# -- anthropic -------------------------------------------------------------

def test_anthropic_request_and_reply(http):
    http["responses"].append((200, {"content": [{"type": "text", "text": "answer"}],
                                    "stop_reason": "end_turn"}))
    out = PROVIDERS["anthropic"].complete("ak", "claude-x", PROMPT)
    assert out == "answer"
    req = http["requests"][0]
    assert req.headers["x-api-key"] == "ak"
    assert req.headers["anthropic-version"]
    body = body_of(req)
    assert body["system"] == "be brief"
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user"]
    assert body["max_tokens"] == 100


def test_anthropic_truncated_reply_is_an_error(http):
    http["responses"].append((200, {"content": [{"type": "text", "text": "half"}],
                                    "stop_reason": "max_tokens"}))
    with pytest.raises(LLMError, match="cut off"):
        PROVIDERS["anthropic"].complete("ak", "m", PROMPT)


def test_anthropic_lists_models(http):
    http["responses"].append((200, {"data": [{"id": "claude-a"}, {"id": "claude-b"}]}))
    assert PROVIDERS["anthropic"].list_models("ak") == ["claude-a", "claude-b"]


# -- openrouter ------------------------------------------------------------

def test_openrouter_disables_reasoning(http):
    http["responses"].append((200, {"choices": [
        {"message": {"content": "answer"}, "finish_reason": "stop"}]}))
    assert PROVIDERS["openrouter"].complete("rk", "some/model", PROMPT) == "answer"
    assert body_of(http["requests"][0])["reasoning"] == {"enabled": False}


def test_openrouter_with_no_model_tries_each_free_fallback(http, monkeypatch):
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    monkeypatch.setenv("JOBS_AGENT_OPENROUTER_MODELS", "a/one, b/two")
    http["responses"] += [
        (429, {"error": {"message": "rate limited"}}),   # a/one
        (429, {"error": {"message": "rate limited"}}),   # a/one, retried
        (200, {"choices": [{"message": {"content": "from two"}, "finish_reason": "stop"}]}),
    ]
    assert PROVIDERS["openrouter"].complete("rk", "", PROMPT) == "from two"
    assert [body_of(r)["model"] for r in http["requests"]] == ["a/one", "a/one", "b/two"]


def test_openrouter_truncated_reply_is_an_error(http):
    http["responses"].append((200, {"choices": [
        {"message": {"content": "thinking..."}, "finish_reason": "length"}]}))
    with pytest.raises(LLMError, match="cut off"):
        PROVIDERS["openrouter"].complete("rk", "some/model", PROMPT)


# -- shared error handling -------------------------------------------------

@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic", "openrouter"])
def test_an_http_error_names_the_status_and_message_but_never_the_key(http, provider):
    http["responses"] += [(401, {"error": {"message": "invalid api key"}})] * 3
    with pytest.raises(LLMError) as e:
        PROVIDERS[provider].complete("SECRET-KEY-123", "m", PROMPT)
    assert "401" in str(e.value)
    assert "invalid api key" in str(e.value)
    assert "SECRET-KEY-123" not in str(e.value)


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic", "openrouter"])
def test_an_empty_reply_is_an_error(http, provider):
    empty = {
        "gemini": {"candidates": [{"content": {"parts": [{"text": "  "}]}}]},
        "openai": {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
        "anthropic": {"content": [], "stop_reason": "end_turn"},
        "openrouter": {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    }[provider]
    http["responses"].append((200, empty))
    with pytest.raises(LLMError, match="no text"):
        PROVIDERS[provider].complete("k", "m", PROMPT)


# -- transient overloads -----------------------------------------------------

@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(base.time, "sleep", slept.append)
    return slept


@pytest.mark.parametrize("status", [429, 503])
def test_an_overloaded_provider_is_retried_once(http, no_sleep, status):
    http["responses"] += [
        (status, {"error": {"message": "busy"}}),
        (200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}),
    ]
    assert PROVIDERS["gemini"].complete("gk", "m", PROMPT) == "answer"
    assert len(http["requests"]) == 2
    assert len(no_sleep) == 1


def test_still_overloaded_after_the_retry_is_an_error(http, no_sleep):
    http["responses"] += [(503, {"error": {"message": "busy"}})] * 2
    with pytest.raises(LLMError, match="503 busy"):
        PROVIDERS["gemini"].complete("gk", "m", PROMPT)
    assert len(http["requests"]) == 2


def test_a_long_retry_after_is_not_waited_out(http, no_sleep):
    """Better to fall through to the next provider than stall a request."""
    def handler(request):
        http["requests"].append(request)
        return httpx.Response(429, json={"error": {"message": "slow down"}},
                              headers={"Retry-After": "60"})

    base.TRANSPORT = httpx.MockTransport(handler)
    with pytest.raises(LLMError, match="429"):
        PROVIDERS["gemini"].complete("gk", "m", PROMPT)
    assert len(http["requests"]) == 1
    assert no_sleep == []


def test_auth_errors_are_not_retried(http, no_sleep):
    http["responses"].append((401, {"error": {"message": "bad key"}}))
    with pytest.raises(LLMError):
        PROVIDERS["anthropic"].complete("ak", "m", PROMPT)
    assert len(http["requests"]) == 1


# -- review fixes ----------------------------------------------------------------

def test_anthropic_overload_529_is_retried(http, no_sleep):
    http["responses"] += [
        (529, {"error": {"message": "overloaded"}}),
        (200, {"content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn"}),
    ]
    assert PROVIDERS["anthropic"].complete("ak", "m", PROMPT) == "answer"


def test_a_negative_retry_after_does_not_crash(http, no_sleep):
    def handler(request):
        http["requests"].append(request)
        if len(http["requests"]) == 1:
            return httpx.Response(503, json={}, headers={"Retry-After": "-3"})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    base.TRANSPORT = httpx.MockTransport(handler)
    assert PROVIDERS["gemini"].complete("gk", "m", PROMPT) == "ok"
    assert no_sleep == [0.0]


def test_openrouter_reports_an_error_inside_a_200(http):
    http["responses"].append((200, {"error": {"message": "upstream moderation", "code": 403}}))
    with pytest.raises(LLMError, match="upstream moderation"):
        PROVIDERS["openrouter"].complete("rk", "some/model", PROMPT)


def test_openrouter_retries_without_the_reasoning_switch_when_a_model_requires_it(http):
    http["responses"] += [
        (400, {"error": {"message": "Reasoning is mandatory for this endpoint"}}),
        (200, {"choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]}),
    ]
    assert PROVIDERS["openrouter"].complete("rk", "openai/o-x", PROMPT) == "answer"
    assert "reasoning" in body_of(http["requests"][0])
    assert "reasoning" not in body_of(http["requests"][1])


def test_gemini_says_why_it_returned_nothing(http):
    http["responses"].append((200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMError, match="SAFETY"):
        PROVIDERS["gemini"].complete("gk", "m", PROMPT)


def test_gemini_model_list_skips_malformed_entries(http):
    http["responses"].append((200, {"models": [
        {"supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
    ]}))
    assert PROVIDERS["gemini"].list_models("gk") == ["gemini-a"]


# -- the time budget -------------------------------------------------------------

def test_requests_get_only_the_time_left_before_the_deadline(http, monkeypatch):
    seen = []
    real_client = httpx.Client

    def spy(*args, **kwargs):
        seen.append(kwargs["timeout"])
        return real_client(*args, **kwargs)

    monkeypatch.setattr(base.httpx, "Client", spy)
    monkeypatch.setattr(base.time, "monotonic", lambda: 100.0)
    http["responses"].append((200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}))
    PROVIDERS["gemini"].complete("gk", "m", Prompt(**{**PROMPT.__dict__, "deadline": 112.0}))
    assert seen == [12.0]


def test_a_spent_deadline_fails_without_a_request(http, monkeypatch):
    monkeypatch.setattr(base.time, "monotonic", lambda: 100.0)
    with pytest.raises(LLMError, match="time"):
        PROVIDERS["gemini"].complete("gk", "m", Prompt(**{**PROMPT.__dict__, "deadline": 99.0}))
    assert http["requests"] == []


def test_no_retry_when_the_wait_would_overrun_the_deadline(http, no_sleep, monkeypatch):
    monkeypatch.setattr(base.time, "monotonic", lambda: 100.0)
    http["responses"].append((503, {"error": {"message": "busy"}}))
    with pytest.raises(LLMError, match="503"):
        PROVIDERS["gemini"].complete("gk", "m", Prompt(**{**PROMPT.__dict__, "deadline": 103.0}))
    assert no_sleep == []
