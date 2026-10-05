"""The fallback client: which providers are tried, in what order, with
whose key. Providers are stubbed — no HTTP."""

from dataclasses import replace

import pytest

from jobs_agent import crypto
from jobs_agent.llm import client as client_mod
from jobs_agent.llm import keys, registry
from jobs_agent.llm.base import LLMError, Message
from jobs_agent.llm.settings import LLMSettings, save_settings

ENV_VARS = [v for spec in registry.PROVIDERS.values() for v in spec.env_vars]


@pytest.fixture(autouse=True)
def no_server_keys(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())


@pytest.fixture
def calls(monkeypatch):
    """Replace every provider's ``complete`` with a stub that records
    (provider, key, model) and replies per ``calls["replies"]``."""
    state = {"seen": [], "replies": {}}

    def stub(provider_id):
        def complete(key, model, prompt):
            state["seen"].append((provider_id, key, model))
            reply = state["replies"].get(provider_id, f"from {provider_id}")
            if isinstance(reply, Exception):
                raise reply
            return reply
        return complete

    patched = {pid: replace(spec, complete=stub(pid)) for pid, spec in registry.PROVIDERS.items()}
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    return state


def ask(store):
    return client_mod.for_user(store).complete(
        "sys", [Message("user", "hi")], temperature=0.5, max_tokens=10)


def test_no_provider_configured_says_where_to_add_one(store, calls):
    with pytest.raises(LLMError, match="Settings"):
        ask(store)


def test_the_server_key_is_used_when_the_user_has_none(store, calls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "server-gemini")
    assert ask(store) == "from gemini"
    assert calls["seen"][0][:2] == ("gemini", "server-gemini")


def test_the_users_own_key_beats_the_server_key(store, calls, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "server-gemini")
    keys.save_user_key(store, "gemini", "user-gemini-key-0001")
    ask(store)
    assert calls["seen"][0][:2] == ("gemini", "user-gemini-key-0001")


def test_providers_are_tried_in_the_users_order_skipping_unconfigured_ones(store, calls):
    keys.save_user_key(store, "openai", "openai-key-00000001")
    keys.save_user_key(store, "anthropic", "anthropic-key-0001")
    save_settings(store, LLMSettings(order=("anthropic", "gemini", "openai"), models={}))
    assert ask(store) == "from anthropic"
    assert [s[0] for s in calls["seen"]] == ["anthropic"]


def test_a_failing_provider_falls_through_to_the_next(store, calls):
    keys.save_user_key(store, "gemini", "gemini-key-00000001")
    keys.save_user_key(store, "openai", "openai-key-00000001")
    calls["replies"]["gemini"] = LLMError("503 overloaded")
    save_settings(store, LLMSettings(order=("gemini", "openai"), models={}))
    assert ask(store) == "from openai"


def test_every_provider_failing_reports_each_reason(store, calls):
    keys.save_user_key(store, "gemini", "gemini-key-00000001")
    keys.save_user_key(store, "openai", "openai-key-00000001")
    calls["replies"]["gemini"] = LLMError("503 overloaded")
    calls["replies"]["openai"] = LLMError("401 bad key")
    with pytest.raises(LLMError) as e:
        ask(store)
    assert "Gemini: 503 overloaded" in str(e.value)
    assert "OpenAI: 401 bad key" in str(e.value)


def test_the_chosen_model_is_passed_through(store, calls):
    keys.save_user_key(store, "anthropic", "anthropic-key-0001")
    save_settings(store, LLMSettings(order=("anthropic",), models={"anthropic": "claude-x"}))
    ask(store)
    assert calls["seen"][0] == ("anthropic", "anthropic-key-0001", "claude-x")


def test_a_provider_left_out_of_the_order_is_still_tried_last(store, calls):
    """The order lists preferences; a key the user added still counts."""
    keys.save_user_key(store, "openai", "openai-key-00000001")
    save_settings(store, LLMSettings(order=("gemini",), models={}))
    assert ask(store) == "from openai"


# -- the server's keys --------------------------------------------------------

def test_the_server_key_always_uses_the_default_model(store, calls, monkeypatch):
    """A user can't point the operator's key at the priciest model there is."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "server-anthropic")
    save_settings(store, LLMSettings(order=("anthropic",), models={"anthropic": "claude-opus-x"}))
    ask(store)
    assert calls["seen"][0] == ("anthropic", "server-anthropic",
                                registry.PROVIDERS["anthropic"].default_model())


def test_a_server_key_failure_is_reported_without_the_providers_detail(store, calls, monkeypatch):
    """Provider errors can quote a masked key or account details; for the
    operator's key, users get a generic message."""
    monkeypatch.setenv("OPENAI_API_KEY", "server-openai")
    calls["replies"]["openai"] = LLMError("401 Incorrect API key provided: sk-proj-****abcd")
    with pytest.raises(LLMError) as e:
        ask(store)
    assert "abcd" not in str(e.value)
    assert "OpenAI: the server's key isn't working" in str(e.value)


def test_a_users_own_key_failure_keeps_the_detail(store, calls):
    keys.save_user_key(store, "openai", "openai-key-00000001")
    calls["replies"]["openai"] = LLMError("401 Incorrect API key provided: sk-****0001")
    with pytest.raises(LLMError, match=r"\*\*\*\*0001"):
        ask(store)


def test_the_client_stops_trying_providers_once_its_budget_is_spent(store, calls, monkeypatch):
    clock = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: next(clock))
    keys.save_user_key(store, "gemini", "gemini-key-00000001")
    keys.save_user_key(store, "openai", "openai-key-00000001")
    calls["replies"]["gemini"] = LLMError("timed out")
    save_settings(store, LLMSettings(order=("gemini", "openai"), models={}))
    with pytest.raises(LLMError, match="ran out of time"):
        ask(store)
    assert [s[0] for s in calls["seen"]] == ["gemini"]


def test_each_provider_gets_the_shared_deadline(store, monkeypatch):
    seen = []
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: 50.0)

    def complete(key, model, prompt):
        seen.append(prompt.deadline)
        return "ok"

    patched = {pid: replace(spec, complete=complete) for pid, spec in registry.PROVIDERS.items()}
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    keys.save_user_key(store, "gemini", "gemini-key-00000001")
    ask(store)
    assert seen == [50.0 + client_mod.BUDGET_SECONDS]


def test_consecutive_same_role_messages_are_merged(store, monkeypatch):
    """A failed chat turn leaves two user messages in a row; providers that
    require alternating roles would reject that."""
    seen = []

    def complete(key, model, prompt):
        seen.append([(m.role, m.content) for m in prompt.messages])
        return "ok"

    patched = {pid: replace(spec, complete=complete) for pid, spec in registry.PROVIDERS.items()}
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    keys.save_user_key(store, "anthropic", "anthropic-key-0001")
    client_mod.for_user(store).complete(
        "sys", [Message("user", "a"), Message("user", "b"), Message("assistant", "c")],
        temperature=0.5, max_tokens=10)
    assert seen == [[("user", "a\n\nb"), ("assistant", "c")]]


def test_analysis_always_uses_each_providers_fast_model(store, calls):
    keys.save_user_key(store, "anthropic", "anthropic-key-0001")
    save_settings(store, LLMSettings(order=("anthropic",), models={"anthropic": "claude-opus-x"}))
    client_mod.for_user(store, fast=True).complete(
        "sys", [Message("user", "hi")], temperature=0.2, max_tokens=10)
    assert calls["seen"][0][2] == registry.PROVIDERS["anthropic"].fast_model()


def test_complete_with_source_names_the_provider_and_model(store, calls):
    keys.save_user_key(store, "openai", "openai-key-00000001")
    text, source = client_mod.for_user(store).complete_with_source(
        "sys", [Message("user", "hi")], temperature=0.2, max_tokens=10)
    assert text == "from openai"
    assert source == f"OpenAI ({registry.PROVIDERS['openai'].default_model()})"


@pytest.mark.parametrize("failure", [
    "503 This model is currently experiencing high demand",
    "429 rate limited", "timed out", "couldn't connect (ConnectError)", "529 overloaded",
])
def test_a_busy_server_key_says_busy_not_broken(store, calls, monkeypatch, failure):
    monkeypatch.setenv("GEMINI_API_KEY", "server-gemini")
    calls["replies"]["gemini"] = LLMError(failure)
    with pytest.raises(LLMError) as e:
        ask(store)
    assert "busy" in str(e.value)
    assert "isn't working" not in str(e.value)
