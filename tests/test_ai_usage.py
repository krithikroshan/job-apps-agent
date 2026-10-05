"""The daily cap on AI calls made with the server's keys."""

import pytest
from dataclasses import replace

from jobs_agent import crypto
from jobs_agent.llm import client as client_mod
from jobs_agent.llm import keys, registry
from jobs_agent.llm.base import LLMError, Message


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for spec in registry.PROVIDERS.values():
        for var in spec.env_vars:
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())
    patched = {pid: replace(spec, complete=lambda key, model, prompt, pid=pid: f"from {pid}")
               for pid, spec in registry.PROVIDERS.items()}
    monkeypatch.setattr(registry, "PROVIDERS", patched)


def ask(store):
    return client_mod.for_user(store).complete("s", [Message("user", "hi")],
                                               temperature=0.1, max_tokens=5)


def test_server_key_calls_stop_at_the_daily_cap(store, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "server-key")
    monkeypatch.setenv("JOBS_AGENT_SERVER_AI_DAILY_CALLS", "3")
    for _ in range(3):
        assert ask(store) == "from gemini"
    with pytest.raises(LLMError, match="daily limit"):
        ask(store)


def test_the_users_own_key_is_never_capped(store, monkeypatch):
    monkeypatch.setenv("JOBS_AGENT_SERVER_AI_DAILY_CALLS", "1")
    keys.save_user_key(store, "openai", "openai-key-00000001")
    for _ in range(5):
        assert ask(store) == "from openai"


def test_the_cap_falls_through_to_the_users_own_key(store, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "server-key")
    monkeypatch.setenv("JOBS_AGENT_SERVER_AI_DAILY_CALLS", "1")
    keys.save_user_key(store, "openai", "openai-key-00000001")
    from jobs_agent.llm.settings import LLMSettings, save_settings
    save_settings(store, LLMSettings(order=("gemini", "openai"), models={}))
    assert ask(store) == "from gemini"
    assert ask(store) == "from openai"


def test_usage_is_per_user(store, monkeypatch):
    from jobs_agent.storage import Store

    monkeypatch.setenv("GEMINI_API_KEY", "server-key")
    monkeypatch.setenv("JOBS_AGENT_SERVER_AI_DAILY_CALLS", "1")
    ask(store)
    other = Store(user_id="00000000-0000-0000-0000-00000000beef", schema=store.schema)
    try:
        assert ask(other) == "from gemini"
    finally:
        other.close()


def test_only_one_analysis_runs_at_a_time_per_user(store):
    with store.exclusive("analyse") as first:
        assert first
        with store.exclusive("analyse") as second:
            assert not second
    with store.exclusive("analyse") as again:
        assert again
