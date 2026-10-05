"""AI settings and stored keys: validation and persistence."""

import pytest

from jobs_agent import crypto
from jobs_agent.llm import keys
from jobs_agent.llm.registry import DEFAULT_ORDER, PROVIDERS
from jobs_agent.llm.settings import (
    LLMSettings,
    SettingsError,
    load_settings,
    model_for,
    parse_settings,
    save_settings,
)


@pytest.fixture(autouse=True)
def server_key(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())


def test_defaults_when_nothing_is_saved(store):
    s = load_settings(store)
    assert s.order == DEFAULT_ORDER
    assert model_for(s, "gemini") == PROVIDERS["gemini"].default_model()


def test_round_trip(store):
    s = LLMSettings(order=("openai", "gemini"), models={"openai": "gpt-x"})
    save_settings(store, s)
    assert load_settings(store) == s


def test_parse_rejects_unknown_providers():
    with pytest.raises(SettingsError, match="unknown"):
        parse_settings({"order": ["gemini", "skynet"], "models": {}})


def test_parse_rejects_duplicates_and_bad_models():
    with pytest.raises(SettingsError):
        parse_settings({"order": ["gemini", "gemini"], "models": {}})
    with pytest.raises(SettingsError):
        parse_settings({"order": ["gemini"], "models": {"gemini": "a b"}})
    with pytest.raises(SettingsError):
        parse_settings({"order": ["gemini"], "models": {"gemini": "x" * 200}})


def test_parse_drops_blank_models_to_mean_default():
    s = parse_settings({"order": ["gemini"], "models": {"gemini": "  "}})
    assert s.models == {}


def test_corrupt_settings_fall_back_to_defaults(store):
    from jobs_agent.llm.settings import DOC_LLM_SETTINGS

    store.set_document(DOC_LLM_SETTINGS, "{nope")
    assert load_settings(store).order == DEFAULT_ORDER


# -- keys --------------------------------------------------------------------

def test_a_saved_key_is_encrypted_at_rest(store):
    keys.save_user_key(store, "openai", "sk-live-abcd1234")
    row = store.get_secret("openai")
    assert "sk-live-abcd1234" not in row["ciphertext"]
    assert row["last4"] == "1234"
    assert keys.user_key(store, "openai") == "sk-live-abcd1234"


def test_keys_are_per_user(store):
    from jobs_agent.storage import Store

    keys.save_user_key(store, "openai", "sk-mine-0000000001")
    other = Store(user_id="00000000-0000-0000-0000-00000000beef", schema=store.schema)
    try:
        assert keys.user_key(other, "openai") is None
    finally:
        other.close()


def test_delete_key(store):
    keys.save_user_key(store, "openai", "sk-x-0000000000001")
    keys.delete_user_key(store, "openai")
    assert keys.user_key(store, "openai") is None


def test_a_key_that_no_longer_decrypts_counts_as_missing(store, monkeypatch):
    keys.save_user_key(store, "openai", "sk-x-0000000000001")
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())
    assert keys.user_key(store, "openai") is None


@pytest.mark.parametrize("bad", ["", "   ", "has space", "x" * 600, "line\nbreak", "sk-short"])
def test_malformed_keys_are_refused(store, bad):
    with pytest.raises(keys.InvalidKey):
        keys.save_user_key(store, "openai", bad)


def test_unknown_provider_is_refused(store):
    with pytest.raises(keys.InvalidKey):
        keys.save_user_key(store, "skynet", "k" * 30)


@pytest.mark.parametrize("model", [
    "../../v1/other", "a/../../x", "/leading-slash", "tunedModels/../x", "x..y/../z",
])
def test_model_ids_cannot_climb_out_of_the_api_path(model):
    with pytest.raises(SettingsError):
        parse_settings({"order": ["gemini"], "models": {"gemini": model}})


def test_vendor_prefixed_model_ids_are_fine():
    s = parse_settings({"order": ["openrouter"],
                        "models": {"openrouter": "qwen/qwen3.8-27b:free"}})
    assert s.models == {"openrouter": "qwen/qwen3.8-27b:free"}
