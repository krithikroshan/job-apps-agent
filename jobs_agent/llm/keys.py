"""Whose key a provider call uses: the signed-in user's own, stored
encrypted, else the server's from the environment."""

from __future__ import annotations

import logging
import os
import re

from .. import crypto
from ..storage import Store
from .registry import PROVIDERS

log = logging.getLogger(__name__)

MIN_KEY_LENGTH = 16   # every supported provider's keys are well over this
MAX_KEY_LENGTH = 512
#: Printable ASCII, no whitespace: what every provider's keys look like.
_KEY_SHAPE = re.compile(r"^[\x21-\x7e]+$")


class InvalidKey(ValueError):
    """A key can't be stored: unknown provider, or not shaped like a key."""


def check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise InvalidKey(f"unknown provider {provider!r}")


def clean_key(key: object) -> str:
    if not isinstance(key, str):
        raise InvalidKey("the key must be text")
    key = key.strip()
    if not key:
        raise InvalidKey("paste a key first")
    if not MIN_KEY_LENGTH <= len(key) <= MAX_KEY_LENGTH or not _KEY_SHAPE.match(key):
        raise InvalidKey("that doesn't look like an API key — check you copied all of it, "
                         "and nothing else")
    return key


def save_user_key(store: Store, provider: str, key: object) -> None:
    """Encrypt and store. Raises :class:`InvalidKey`, or
    :class:`~jobs_agent.crypto.CryptoError` if the server can't encrypt."""
    check_provider(provider)
    key = clean_key(key)
    store.set_secret(provider, crypto.encrypt(key), key[-4:])


def delete_user_key(store: Store, provider: str) -> None:
    check_provider(provider)
    store.delete_secret(provider)


def user_key(store: Store, provider: str) -> str | None:
    row = store.get_secret(provider)
    if not row:
        return None
    try:
        return crypto.decrypt(row["ciphertext"])
    except crypto.CryptoError as e:
        log.warning("ignoring stored %s key: %s", provider, e)
        return None


def server_key(provider: str) -> str | None:
    for var in PROVIDERS[provider].env_vars:
        if os.getenv(var):
            return os.environ[var]
    return None


#: Where a resolved key came from.
USER, SERVER = "user", "server"


def resolve_key(store: Store, provider: str) -> tuple[str, str] | None:
    """``(key, USER | SERVER)``, or None if neither has one."""
    if (key := user_key(store, provider)):
        return key, USER
    if (key := server_key(provider)):
        return key, SERVER
    return None
