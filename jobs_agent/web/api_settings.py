"""Settings page endpoints: AI provider keys, order, and models.

Same contract as api.py — plain functions of ``(store, request)``. A key is
accepted here and stored encrypted; no response ever contains one, only its
last four characters.
"""

from __future__ import annotations

from .. import crypto
import logging

from ..llm import LLMError, registry
from ..llm.client import SERVER_KEY_FAILED
from ..llm.keys import InvalidKey, check_provider, clean_key, delete_user_key, \
    save_user_key, server_key, user_key
from ..llm.settings import SettingsError, full_order, load_settings, parse_settings, \
    save_settings
from ..storage import Store
from .api import Json, Request, error

#: The model list shown after a test; OpenRouter alone offers hundreds.
MAX_MODELS_LISTED = 300

log = logging.getLogger(__name__)


def get_llm_settings(store: Store, req: Request) -> Json:
    settings = load_settings(store)
    on_file = {row["provider"]: row["last4"] for row in store.list_secrets()}
    providers = [
        {
            "id": spec.id,
            "label": spec.label,
            "key_url": spec.key_url,
            "user_key_last4": on_file.get(spec.id),
            # On file but undecryptable (APP_ENCRYPTION_KEY changed): it's
            # being skipped, and the user needs to enter it again.
            "user_key_unreadable": spec.id in on_file and user_key(store, spec.id) is None,
            "has_server_key": server_key(spec.id) is not None,
            "model": settings.models.get(spec.id, ""),
            "default_model": spec.default_model(),
            "fast_model": spec.fast_model(),
        }
        for spec in registry.PROVIDERS.values()
    ]
    return Json({
        "providers": providers,
        "order": full_order(settings),
        "can_store_keys": crypto.is_configured(),
    })


def post_llm_key(store: Store, req: Request) -> Json:
    try:
        save_user_key(store, str(req.payload.get("provider") or ""), req.payload.get("key"))
    except (InvalidKey, crypto.CryptoError) as e:
        return error(str(e))
    return Json({"ok": True})


def post_llm_key_delete(store: Store, req: Request) -> Json:
    try:
        delete_user_key(store, str(req.payload.get("provider") or ""))
    except InvalidKey as e:
        return error(str(e))
    return Json({"ok": True})


def post_llm_test(store: Store, req: Request) -> Json:
    """Check a key by listing the models it can use — free, no tokens spent.
    Tests the key typed into the page if there is one, else the stored one,
    else the server's."""
    provider = str(req.payload.get("provider") or "")
    try:
        check_provider(provider)
        typed = req.payload.get("key")
        if typed:
            key, source = clean_key(typed), "typed"
        elif (stored := user_key(store, provider)):
            key, source = stored, "yours"
        elif (shared := server_key(provider)):
            key, source = shared, "server"
        else:
            return error("No key to test — paste one first.")
        models = registry.PROVIDERS[provider].list_models(key)
    except InvalidKey as e:
        return error(str(e))
    except LLMError as e:
        label = registry.PROVIDERS[provider].label
        if source == "server":
            log.warning("server %s key failed its test: %s", label, e)
            return error(f"{label}: {SERVER_KEY_FAILED}")
        if str(e).startswith(("401", "403")):
            return error(f"{label} rejected the key: {e}")
        return error(f"Couldn't check the key with {label}: {e}")
    return Json({"ok": True, "models": models[:MAX_MODELS_LISTED], "source": source})


def post_llm_settings(store: Store, req: Request) -> Json:
    try:
        settings = parse_settings(req.payload)
    except SettingsError as e:
        return error(str(e))
    save_settings(store, settings)
    return Json({"ok": True})
