"""Encryption at rest for secrets users give us (their AI provider keys).

Fernet (AES-128-CBC + HMAC-SHA256) under one server-held key in
``APP_ENCRYPTION_KEY``. The database only ever sees ciphertext, so a leaked
dump or a stray ``SELECT *`` doesn't leak anyone's keys — the server key has
to leak too. Generate one with ``python -m jobs_agent gen-key``.

Rotating the server key makes every stored secret undecryptable; callers
treat that as "no key stored", and users re-enter their keys.
"""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

ENV_VAR = "APP_ENCRYPTION_KEY"


class CryptoError(RuntimeError):
    """The server key is missing or malformed, or a token won't decrypt."""


def generate_key() -> str:
    return Fernet.generate_key().decode()


def is_configured() -> bool:
    return bool(os.getenv(ENV_VAR))


def _fernet() -> Fernet:
    raw = os.getenv(ENV_VAR)
    if not raw:
        raise CryptoError(
            f"{ENV_VAR} is not set, so keys can't be stored. Generate one with "
            f"`python -m jobs_agent gen-key` and add it to .env (and to the "
            f"Vercel project's environment variables for deployments).")
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        raise CryptoError(
            f"{ENV_VAR} isn't a valid key. Generate a new one with "
            f"`python -m jobs_agent gen-key`.") from None


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise CryptoError("stored secret can't be decrypted with the current "
                          f"{ENV_VAR}") from None
