"""HTTP plumbing: route table, request parsing, response serialisation.

Five pages:

  /login       sign in with an existing account
  /signup      create an account
  /            job review queue — fetch, filter, shortlist/reject, and run
               the review-and-approve workflow below
  /documents   Profile page — candidate name, CV (uploaded as .docx or .pdf,
               text extracted), cover-letter template, and the scoring
               profile, stored once and reused for every draft
  /settings    AI providers — each user's own keys (stored encrypted), the
               order they're tried in, and the model asked for at each

Every page and API route except /login, /signup, and /static/* requires a
signed-in session (see auth.py, backed by Supabase Auth) and operates on
that account's own data only — see storage/store.py, where every query is
scoped to the signed-in user's id.

Review workflow: "Prepare application" drafts a letter tailored to that
posting (see letters/) and sets it to "drafted". A human reads it, edits it,
and clicks "Approve". Only once a posting is "approved" can it be marked
"submitted" — api.py rejects any attempt to skip that step. Nothing in this
tool ever submits anything itself; "submitted" just records that a human did
so elsewhere, so it stops resurfacing in the queue.

Stdlib only (http.server) beyond what fetch/queue/stats already need.
Drafting additionally needs an AI provider key — the user's own, set on
/settings, or the server's in the environment.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler
from typing import Mapping
from urllib.parse import parse_qs, urlparse

from ..profile import load_profile
from ..storage import DOC_CANDIDATE_NAME, open_store
from . import api, api_ai, api_settings, auth, pages

def is_cross_site(headers: Mapping[str, str]) -> bool:
    """True for a POST another site's page made the browser send.

    The session cookies are SameSite=Lax, which already keeps them off
    cross-site POSTs in current browsers; this is the second check, so a
    forged request can't overwrite a stored API key even where that fails.
    Requests with neither header (curl, tests) aren't from a browser page.
    """
    fetch_site = headers.get("Sec-Fetch-Site")
    if fetch_site:
        return fetch_site not in ("same-origin", "none")
    origin = headers.get("Origin")
    if origin is None:
        return False
    return urlparse(origin).netloc != headers.get("Host", "")


def confirm_redirect_url(headers: Mapping[str, str]) -> str:
    """Where Supabase's signup-confirmation email should send the user: this
    deployment's /login page. ``APP_URL`` pins it; otherwise it comes from the
    request (Vercel sets X-Forwarded-Proto). Supabase only honours URLs on the
    project's Redirect URLs allowlist, so a spoofed Host can't redirect
    anywhere else; it falls back to the project's Site URL."""
    base = os.getenv("APP_URL", "").rstrip("/")
    if not base:
        scheme = headers.get("X-Forwarded-Proto", "http").split(",")[0].strip()
        base = f"{scheme}://{headers.get('Host', '')}"
    return f"{base}/login"


#: path -> endpoint, per method. Adding an endpoint means one entry here and
#: one function in api.py.
GET_ROUTES = {
    "/api/stats": api.get_stats,
    "/api/queue": api.get_queue,
    "/api/documents": api.get_documents,
    "/api/profile": api.get_profile,
    "/api/presets": api.get_presets,
    "/api/cv/file": api.get_cv_file,
    "/api/llm/settings": api_settings.get_llm_settings,
    "/api/analysis": api_ai.get_analysis_status,
}

POST_ROUTES = {
    "/api/fetch": api.post_fetch,
    "/api/status": api.post_status,
    "/api/delete": api.post_delete,
    "/api/draft": api.post_draft,
    "/api/redraft": api.post_redraft,
    "/api/letter": api.post_letter,
    "/api/cv": api.post_cv,
    "/api/documents": api.post_documents,
    "/api/profile": api.post_profile,
    "/api/profile/reset": api.post_profile_reset,
    "/api/profile/chat": api.post_profile_chat,
    "/api/llm/key": api_settings.post_llm_key,
    "/api/llm/key/delete": api_settings.post_llm_key_delete,
    "/api/llm/test": api_settings.post_llm_test,
    "/api/llm/settings": api_settings.post_llm_settings,
    "/api/analyse": api_ai.post_analyse,
    "/api/analyse/retry": api_ai.post_analyse_retry,
    "/api/suggest/profile": api_ai.post_suggest_profile,
    "/api/search": api_ai.post_search,
}


class Handler(BaseHTTPRequestHandler):
    #: Postgres connection string override; None uses DATABASE_URL from the
    #: environment, resolved lazily by Store on each request.
    db: str | None = None

    def log_message(self, fmt, *args) -> None:  # quiet the default access log
        pass

    # -- responses --------------------------------------------------------

    def _send(self, body: bytes, content_type: str, status: int = 200,
              extra_headers: list[tuple[str, str]] | None = None) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in (extra_headers or []):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The browser hung up first (navigated away, reloaded). Normal;
            # there's no one left to send the response to.
            self.close_connection = True

    def _send_result(self, result, extra_headers: list[tuple[str, str]] | None = None) -> None:
        """Serialise whatever an endpoint returned."""
        if isinstance(result, api.File):
            headers = list(extra_headers or [])
            headers.append(("Content-Disposition",
                            f'attachment; filename="{result.filename}"'))
            self._send(result.data, result.content_type, extra_headers=headers)
        else:
            self._send(json.dumps(result.body).encode(),
                       "application/json", result.status, extra_headers)

    def _send_html(self, html: str, extra_headers: list[tuple[str, str]] | None = None) -> None:
        self._send(html.encode(), "text/html; charset=utf-8", extra_headers=extra_headers)

    def _not_found(self) -> None:
        self._send(json.dumps({"error": "not found"}).encode(),
                   "application/json", 404)

    def _candidate_name(self, store) -> str:
        """The name shown in the nav on every authenticated page, not just
        the Profile page it's set on."""
        name = store.get_document(DOC_CANDIDATE_NAME).strip()
        return name or load_profile(store).name

    def _unauthorized(self) -> None:
        self._send(json.dumps({"error": "not authenticated"}).encode(),
                   "application/json", 401)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- auth ---------------------------------------------------------------

    def _authenticate(self) -> auth.AuthResult | None:
        cookies = auth.parse_cookies(self.headers.get("Cookie"))
        return auth.resolve(cookies)

    def _session_headers(self, result: auth.AuthResult) -> list[tuple[str, str]]:
        """Set-Cookie headers for a session that was silently refreshed while
        resolving this request; empty when nothing needs to change."""
        if not result.refreshed:
            return []
        return [("Set-Cookie", v) for v in auth.set_cookie_headers(result.refreshed)]

    def _handle_signup(self, payload: dict) -> None:
        email, password = (payload.get("email") or "").strip(), payload.get("password") or ""
        if not email or not password:
            self._send(json.dumps({"error": "Email and password are required."}).encode(),
                       "application/json", 400)
            return
        try:
            session = auth.sign_up(email, password,
                                   redirect_to=confirm_redirect_url(self.headers))
        except auth.AuthError as e:
            self._send(json.dumps({"error": str(e)}).encode(), "application/json", 400)
            return
        if session is None:
            self._send(json.dumps({
                "ok": True,
                "message": "Check your email to confirm your account, then log in.",
            }).encode(), "application/json")
            return
        headers = [("Set-Cookie", v) for v in auth.set_cookie_headers(session)]
        self._send(json.dumps({"ok": True}).encode(), "application/json", 200, headers)

    def _handle_login(self, payload: dict) -> None:
        email, password = (payload.get("email") or "").strip(), payload.get("password") or ""
        if not email or not password:
            self._send(json.dumps({"error": "Email and password are required."}).encode(),
                       "application/json", 400)
            return
        try:
            session = auth.sign_in(email, password)
        except auth.AuthError as e:
            self._send(json.dumps({"error": str(e)}).encode(), "application/json", 400)
            return
        headers = [("Set-Cookie", v) for v in auth.set_cookie_headers(session)]
        self._send(json.dumps({"ok": True}).encode(), "application/json", 200, headers)

    def _handle_logout(self) -> None:
        cookies = auth.parse_cookies(self.headers.get("Cookie"))
        access = cookies.get(auth.ACCESS_COOKIE)
        if access:
            auth.sign_out(access)
        headers = [("Set-Cookie", v) for v in auth.clear_cookie_headers()]
        self._send(json.dumps({"ok": True}).encode(), "application/json", 200, headers)

    # -- routing ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/static/"):
            asset = pages.static_asset(path[len("/static/"):])
            if asset is None:
                self._not_found()
                return
            body, content_type = asset
            self._send(body, content_type)
            return

        if path == "/login":
            self._send_html(pages.login_page())
            return

        if path == "/signup":
            self._send_html(pages.signup_page())
            return

        result = self._authenticate()
        if result is None:
            if path.startswith("/api/"):
                self._unauthorized()
            else:
                self._redirect("/login")
            return
        extra_headers = self._session_headers(result)

        if path == "/":
            with open_store(self.db, user_id=result.user_id) as store:
                name = self._candidate_name(store)
            self._send_html(pages.queue_page(name), extra_headers)
            return

        if path == "/documents":
            with open_store(self.db, user_id=result.user_id) as store:
                name = self._candidate_name(store)
            self._send_html(pages.documents_page(name), extra_headers)
            return

        if path == "/settings":
            with open_store(self.db, user_id=result.user_id) as store:
                name = self._candidate_name(store)
            self._send_html(pages.settings_page(name), extra_headers)
            return

        endpoint = GET_ROUTES.get(path)
        if endpoint is None:
            self._not_found()
            return
        request = api.Request(query=parse_qs(parsed.query))
        with open_store(self.db, user_id=result.user_id) as store:
            self._send_result(endpoint(store, request), extra_headers)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if is_cross_site(self.headers):
            self._send_result(api.error("cross-site request refused", 403))
            return
        payload = self._read_json()

        if path == "/api/signup":
            self._handle_signup(payload)
            return
        if path == "/api/login":
            self._handle_login(payload)
            return
        if path == "/api/logout":
            self._handle_logout()
            return

        endpoint = POST_ROUTES.get(path)
        if endpoint is None:
            self._not_found()
            return

        result = self._authenticate()
        if result is None:
            self._unauthorized()
            return
        extra_headers = self._session_headers(result)

        request = api.Request(payload=payload)
        with open_store(self.db, user_id=result.user_id) as store:
            self._send_result(endpoint(store, request), extra_headers)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
