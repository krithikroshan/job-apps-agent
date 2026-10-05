"""Supabase Auth (GoTrue), over its REST API.

Handles signup, login, logout, and the session cookies that carry a signed-in
user across requests. No JWT verification happens locally: every request
that needs to know who is signed in calls Supabase's ``/auth/v1/user`` with
the access token, which is the simplest correct way to validate a token
without holding the project's JWT secret. This app's traffic is small enough
that the extra round trip per request is not a concern.
"""

from __future__ import annotations

import os
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


def sign_up(email: str, password: str) -> Session | None:
    """Create an account. Returns the new session, or ``None`` if the
    project requires email confirmation before one is issued."""
    r = httpx.post(f"{supabase_url()}/auth/v1/signup",
                    headers=_headers(), json={"email": email, "password": password})
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
    httpx.post(f"{supabase_url()}/auth/v1/logout", headers=_headers(access_token))


def _refresh(refresh_token: str) -> Session | None:
    r = httpx.post(f"{supabase_url()}/auth/v1/token?grant_type=refresh_token",
                    headers=_headers(), json={"refresh_token": refresh_token})
    if r.status_code >= 400:
        return None
    return _session_from(r.json())


def _get_user(access_token: str) -> dict | None:
    r = httpx.get(f"{supabase_url()}/auth/v1/user", headers=_headers(access_token))
    if r.status_code >= 400:
        return None
    return r.json()


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
