"""Encryption of stored API keys."""

import pytest

from jobs_agent import crypto


@pytest.fixture
def key(monkeypatch):
    k = crypto.generate_key()
    monkeypatch.setenv(crypto.ENV_VAR, k)
    return k


def test_round_trip(key):
    token = crypto.encrypt("sk-secret")
    assert token != "sk-secret"
    assert "sk-secret" not in token
    assert crypto.decrypt(token) == "sk-secret"


def test_a_different_server_key_cannot_decrypt(key, monkeypatch):
    token = crypto.encrypt("sk-secret")
    monkeypatch.setenv(crypto.ENV_VAR, crypto.generate_key())
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt(token)


def test_missing_server_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv(crypto.ENV_VAR, raising=False)
    assert not crypto.is_configured()
    with pytest.raises(crypto.CryptoError, match=crypto.ENV_VAR):
        crypto.encrypt("x")


def test_a_malformed_server_key_is_a_clear_error(monkeypatch):
    monkeypatch.setenv(crypto.ENV_VAR, "not-a-fernet-key")
    with pytest.raises(crypto.CryptoError, match=crypto.ENV_VAR):
        crypto.encrypt("x")
