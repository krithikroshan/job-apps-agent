"""AI providers behind one interface: adapters per provider, each user's
keys (encrypted) and preferences, and a client that falls back between them.

Callers need only :func:`for_user` and :class:`Message`; everything that
fails raises :class:`LLMError` with a message safe to show the user.
"""

from .base import LLMError, Message
from .client import Client, for_user

__all__ = ["Client", "LLMError", "Message", "for_user"]
