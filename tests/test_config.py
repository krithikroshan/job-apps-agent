"""Supabase key lookup: the publishable key, with the legacy anon key as a
fallback for projects that haven't migrated."""

import pytest

from jobs_agent.config import supabase_api_key


def test_prefers_the_publishable_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_x")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "legacy")
    assert supabase_api_key() == "sb_publishable_x"


def test_falls_back_to_the_legacy_anon_key(monkeypatch):
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "legacy")
    assert supabase_api_key() == "legacy"


def test_neither_set_names_the_variable_to_add(monkeypatch):
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_PUBLISHABLE_KEY"):
        supabase_api_key()
