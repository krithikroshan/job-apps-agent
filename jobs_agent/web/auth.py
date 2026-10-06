"""Supabase Auth (GoTrue), over its REST API.

Handles signup, login, logout, and the session cookies that carry a signed-in
user across requests. No JWT verification happens locally: a token is
validated by calling Supabase's ``/auth/v1/user`` with it, which is the
simplest correct way without holding the project's JWT secret. A token that
passes is then trusted for :data:`VERIFIED_TTL_SECONDS` in this process —
every page load makes several API calls, and a fresh HTTPS round trip to
Supabase for each one made the whole site crawl.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie

import httpx

from ..config import supabase_api_key, supabase_url

ACCESS_COOKIE = "sb_access_token"
REFRESH_COOKIE = "sb_refresh_token"

#: Access tokens are short-lived (Supabase's default is 1 hour) and get
#: silently refreshed; the cookie lifetime that actually matters is this one,
#: so a session survives a browser restart.
COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days

#: How long a token Supabase accepted is trusted without asking again. A
#: token revoked elsewhere (signed out on another device) keeps working on
#: this process for at most this long; signing out here forgets it at once.
VERIFIED_TTL_SECONDS = 60
#: Most tokens remembered; past this the oldest are forgotten first.
VERIFIED_MAX = 1000

#: sha256(access token) -> (user, monotonic time it was verified).
_VERIFIED: dict[str, tuple[dict, float]] = {}
_VERIFIED_LOCK = threading.Lock()
#: Kept open between requests so each check reuses the TLS connection.
_CLIENT = httpx.Client(timeout=10)


class AuthError(Exception):
    """A signup/login call was rejected by Supabase. The message is
    user-facing."""


@dataclass
class Session:
    access_token: str
    refresh_token: str
    user_id: str
    email: str


@dataclass
class AuthResult:
    user_id: str
    email: str
    #: Set when an expired access token was transparently refreshed while
    #: resolving this request — the caller must send these as new cookies.
    refreshed: Session | None = None


def _headers(access_token: str | None = None) -> dict[str, str]:
    headers = {"apikey": supabase_api_key(), "Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _session_from(data: dict) -> Session:
    user = data.get("user") or {}
    return Session(
        access_token=data["access_token"],
        refresh_token=data["refresh_token"],
        user_id=user.get("id", ""),
        email=user.get("email", ""),
    )


def sign_up(email: str, password: str, redirect_to: str | None = None) -> Session | None:
    """Create an account. Returns the new session, or ``None`` if the
    project requires email confirmation before one is issued.
    ``redirect_to`` is where the confirmation email's link lands."""
    r = httpx.post(f"{supabase_url()}/auth/v1/signup",
                    headers=_headers(), json={"email": email, "password": password},
                    params={"redirect_to": redirect_to} if redirect_to else None)
    data = r.json()
    if r.status_code >= 400:
        raise AuthError(data.get("msg") or data.get("error_description")
                        or "Could not create that account.")
    if not data.get("access_token"):
        return None
    return _session_from(data)


def sign_in(email: str, password: str) -> Session:
    r = httpx.post(f"{supabase_url()}/auth/v1/token?grant_type=password",
                    headers=_headers(), json={"email": email, "password": password})
    data = r.json()
    if r.status_code >= 400:
        raise AuthError(data.get("error_description") or data.get("msg")
                        or "Invalid email or password.")
    return _session_from(data)


def sign_out(access_token: str) -> None:
    with _VERIFIED_LOCK:
        _VERIFIED.pop(_token_key(access_token), None)
    httpx.post(f"{supabase_url()}/auth/v1/logout", headers=_headers(access_token))


def _refresh(refresh_token: str) -> Session | None:
    r = httpx.post(f"{supabase_url()}/auth/v1/token?grant_type=refresh_token",
                    headers=_headers(), json={"refresh_token": refresh_token})
    if r.status_code >= 400:
        return None
    return _session_from(r.json())


def _token_key(access_token: str) -> str:
    # Hashed so the cache never holds a usable token.
    return hashlib.sha256(access_token.encode()).hexdigest()


def _fetch_user(access_token: str) -> dict | None:
    r = _CLIENT.get(f"{supabase_url()}/auth/v1/user", headers=_headers(access_token))
    if r.status_code >= 400:
        return None
    return r.json()


def _get_user(access_token: str) -> dict | None:
    """The token's user, from the cache while it's fresh, else Supabase.
    Rejections aren't cached, so a token that failed is asked about again."""
    key, now = _token_key(access_token), time.monotonic()
    with _VERIFIED_LOCK:
        hit = _VERIFIED.get(key)
        if hit and now - hit[1] < VERIFIED_TTL_SECONDS:
            return hit[0]
    user = _fetch_user(access_token)
    if user:
        with _VERIFIED_LOCK:
            _VERIFIED.pop(key, None)  # re-inserted last, so eviction stays oldest-first
            _VERIFIED[key] = (user, now)
            while len(_VERIFIED) > VERIFIED_MAX:
                del _VERIFIED[next(iter(_VERIFIED))]
    return user


def resolve(cookies: dict[str, str]) -> AuthResult | None:
    """Identify the signed-in user from request cookies, refreshing an
    expired access token with the refresh token when needed. ``None`` means
    not signed in."""
    access = cookies.get(ACCESS_COOKIE)
    if access:
        user = _get_user(access)
        if user:
            return AuthResult(user_id=user["id"], email=user.get("email", ""))

    refresh_token = cookies.get(REFRESH_COOKIE)
    if refresh_token:
        session = _refresh(refresh_token)
        if session:
            return AuthResult(user_id=session.user_id, email=session.email,
                              refreshed=session)

    return None


# -- cookies ----------------------------------------------------------------

def parse_cookies(header: str | None) -> dict[str, str]:
    jar: SimpleCookie = SimpleCookie()
    if header:
        jar.load(header)
    return {name: morsel.value for name, morsel in jar.items()}


def _secure_flag() -> str:
    """Vercel sets ``VERCEL`` in every deployment; ``python -m jobs_agent
    serve`` never does, so the ``Secure`` flag only applies where the
    connection is actually HTTPS."""
    return "; Secure" if os.getenv("VERCEL") else ""


def set_cookie_headers(session: Session) -> list[str]:
    secure = _secure_flag()
    return [
        f"{ACCESS_COOKIE}={session.access_token}; Path=/; HttpOnly; "
        f"SameSite=Lax; Max-Age={COOKIE_MAX_AGE}{secure}",
        f"{REFRESH_COOKIE}={session.refresh_token}; Path=/; HttpOnly; "
        f"SameSite=Lax; Max-Age={COOKIE_MAX_AGE}{secure}",
    ]


def clear_cookie_headers() -> list[str]:
    secure = _secure_flag()
    return [
        f"{ACCESS_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}",
        f"{REFRESH_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}",
    ]
