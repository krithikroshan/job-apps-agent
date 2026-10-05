"""Settings page endpoints. The point of most of these: a key goes in, and
never comes back out."""

import json

import pytest

from jobs_agent import crypto
from jobs_agent.llm import keys, registry
from jobs_agent.llm.base import LLMError
from jobs_agent.web import api, api_settings


def req(**payload):
    return api.Request(payload=payload)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())
    for spec in registry.PROVIDERS.values():
        for var in spec.env_vars:
            monkeypatch.delenv(var, raising=False)


def test_settings_list_every_provider_without_any_key(store, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "SERVER-SECRET")
    keys.save_user_key(store, "openai", "sk-USER-SECRET-000-9876")
    body = api_settings.get_llm_settings(store, api.Request()).body
    dumped = json.dumps(body)
    assert "SERVER-SECRET" not in dumped
    assert "USER-SECRET" not in dumped

    by_id = {p["id"]: p for p in body["providers"]}
    assert set(by_id) == set(registry.PROVIDERS)
    assert by_id["openai"]["user_key_last4"] == "9876"
    assert by_id["gemini"]["has_server_key"] is True
    assert by_id["anthropic"]["user_key_last4"] is None
    assert body["order"] == list(registry.DEFAULT_ORDER)
    assert body["can_store_keys"] is True


def test_saving_a_key(store):
    res = api_settings.post_llm_key(store, req(provider="anthropic", key=" sk-ant-0000000001234 "))
    assert res.status == 200
    assert keys.user_key(store, "anthropic") == "sk-ant-0000000001234"
    assert "sk-ant-0000000001234" not in json.dumps(res.body)


def test_saving_a_key_without_a_server_encryption_key_is_refused(store, monkeypatch):
    monkeypatch.delenv(crypto.ENV_VAR)
    res = api_settings.post_llm_key(store, req(provider="anthropic", key="sk-ant-0000000000001"))
    assert res.status == 400
    assert crypto.ENV_VAR in res.body["error"]


@pytest.mark.parametrize("payload", [
    {"provider": "skynet", "key": "k"},
    {"provider": "openai", "key": ""},
    {"provider": "openai", "key": ["k"]},
    {"provider": "openai"},
])
def test_bad_key_payloads_are_400s(store, payload):
    assert api_settings.post_llm_key(store, req(**payload)).status == 400


def test_deleting_a_key(store):
    keys.save_user_key(store, "openai", "sk-0000000000000001")
    assert api_settings.post_llm_key_delete(store, req(provider="openai")).status == 200
    assert keys.user_key(store, "openai") is None


def test_testing_a_typed_key_lists_its_models(store, monkeypatch):
    seen = []

    def list_models(key):
        seen.append(key)
        return ["claude-a", "claude-b"]

    from dataclasses import replace
    patched = dict(registry.PROVIDERS)
    patched["anthropic"] = replace(patched["anthropic"], list_models=list_models)
    monkeypatch.setattr(registry, "PROVIDERS", patched)

    res = api_settings.post_llm_test(store, req(provider="anthropic", key="sk-typed-00000000001"))
    assert res.status == 200
    assert res.body == {"ok": True, "models": ["claude-a", "claude-b"], "source": "typed"}
    assert seen == ["sk-typed-00000000001"]


def test_testing_falls_back_to_the_stored_key(store, monkeypatch):
    from dataclasses import replace
    seen = []
    patched = dict(registry.PROVIDERS)
    patched["openai"] = replace(patched["openai"],
                                list_models=lambda key: seen.append(key) or ["gpt-x"])
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    keys.save_user_key(store, "openai", "sk-stored-0000000001")

    res = api_settings.post_llm_test(store, req(provider="openai"))
    assert res.status == 200 and res.body["source"] == "yours"
    assert seen == ["sk-stored-0000000001"]


def test_a_failing_test_is_a_400_with_the_reason(store, monkeypatch):
    from dataclasses import replace

    def fail(key):
        raise LLMError("401 invalid api key")

    patched = dict(registry.PROVIDERS)
    patched["openai"] = replace(patched["openai"], list_models=fail)
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    res = api_settings.post_llm_test(store, req(provider="openai", key="sk-bad-00000000000001"))
    assert res.status == 400
    assert "401" in res.body["error"]


def test_testing_with_no_key_anywhere_is_a_400(store):
    res = api_settings.post_llm_test(store, req(provider="openai"))
    assert res.status == 400


def test_saving_order_and_models(store):
    res = api_settings.post_llm_settings(store, req(
        order=["anthropic", "gemini"], models={"anthropic": "claude-x"}))
    assert res.status == 200
    body = api_settings.get_llm_settings(store, api.Request()).body
    assert body["order"][:2] == ["anthropic", "gemini"]
    assert {p["id"]: p["model"] for p in body["providers"]}["anthropic"] == "claude-x"


def test_bad_settings_are_a_400(store):
    res = api_settings.post_llm_settings(store, req(order=["skynet"], models={}))
    assert res.status == 400


def test_a_failing_server_key_test_hides_the_providers_detail(store, monkeypatch):
    from dataclasses import replace

    def fail(key):
        raise LLMError("401 Incorrect API key provided: sk-proj-****abcd")

    monkeypatch.setenv("OPENAI_API_KEY", "server-openai-key-0001")
    patched = dict(registry.PROVIDERS)
    patched["openai"] = replace(patched["openai"], list_models=fail)
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    res = api_settings.post_llm_test(store, req(provider="openai"))
    assert res.status == 400
    assert "abcd" not in res.body["error"]
    assert "server" in res.body["error"]


def test_a_test_that_fails_for_a_non_key_reason_says_so(store, monkeypatch):
    from dataclasses import replace

    def fail(key):
        raise LLMError("timed out")

    patched = dict(registry.PROVIDERS)
    patched["openai"] = replace(patched["openai"], list_models=fail)
    monkeypatch.setattr(registry, "PROVIDERS", patched)
    res = api_settings.post_llm_test(store, req(provider="openai", key="sk-typed-00000000001"))
    assert res.status == 400
    assert "rejected" not in res.body["error"]
    assert "timed out" in res.body["error"]


def test_a_stored_key_that_no_longer_decrypts_is_flagged(store, monkeypatch):
    keys.save_user_key(store, "openai", "sk-USER-SECRET-000-9876")
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())
    body = api_settings.get_llm_settings(store, api.Request()).body
    openai = {p["id"]: p for p in body["providers"]}["openai"]
    assert openai["user_key_last4"] == "9876"
    assert openai["user_key_unreadable"] is True
