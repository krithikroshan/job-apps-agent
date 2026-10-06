"""Cookie plumbing around Supabase Auth sessions — no network calls here,
those live behind httpx and are exercised through the running server."""

from jobs_agent.web import auth


def test_parse_cookies_reads_both_session_cookies():
    header = f"{auth.ACCESS_COOKIE}=abc; {auth.REFRESH_COOKIE}=xyz; other=1"
    cookies = auth.parse_cookies(header)
    assert cookies[auth.ACCESS_COOKIE] == "abc"
    assert cookies[auth.REFRESH_COOKIE] == "xyz"


def test_parse_cookies_handles_no_header():
    assert auth.parse_cookies(None) == {}


def test_set_cookie_headers_are_http_only_and_carry_both_tokens():
    session = auth.Session(access_token="a", refresh_token="r",
                           user_id="u1", email="a@b.com")
    headers = auth.set_cookie_headers(session)
    assert any(h.startswith(f"{auth.ACCESS_COOKIE}=a;") and "HttpOnly" in h for h in headers)
    assert any(h.startswith(f"{auth.REFRESH_COOKIE}=r;") and "HttpOnly" in h for h in headers)


def test_secure_flag_only_set_on_vercel(monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    session = auth.Session(access_token="a", refresh_token="r", user_id="u1", email="")
    assert not any("Secure" in h for h in auth.set_cookie_headers(session))

    monkeypatch.setenv("VERCEL", "1")
    assert all("Secure" in h for h in auth.set_cookie_headers(session))


def test_clear_cookie_headers_expire_both_tokens():
    headers = auth.clear_cookie_headers()
    assert any(h.startswith(f"{auth.ACCESS_COOKIE}=;") and "Max-Age=0" in h for h in headers)
    assert any(h.startswith(f"{auth.REFRESH_COOKIE}=;") and "Max-Age=0" in h for h in headers)


# -- cross-site request check -------------------------------------------------

from jobs_agent.web.handler import is_cross_site  # noqa: E402


def test_same_origin_and_headerless_posts_are_allowed():
    assert not is_cross_site({"Host": "app.example", "Origin": "https://app.example"})
    assert not is_cross_site({"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"})
    assert not is_cross_site({"Host": "app.example", "Sec-Fetch-Site": "same-origin"})
    assert not is_cross_site({"Host": "app.example"})  # curl, tests, old browsers


def test_cross_site_posts_are_refused():
    assert is_cross_site({"Host": "app.example", "Origin": "https://evil.example"})
    assert is_cross_site({"Host": "app.example", "Sec-Fetch-Site": "cross-site"})
    assert is_cross_site({"Host": "app.example", "Origin": "null"})


def test_the_server_refuses_a_cross_site_post_before_anything_else():
    """End to end over a socket: the check sits in do_POST itself."""
    import threading
    from http.server import ThreadingHTTPServer

    import httpx

    from jobs_agent.web.handler import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/llm/key"
    try:
        evil = httpx.post(url, json={}, headers={"Origin": "https://evil.example"})
        assert evil.status_code == 403
        assert evil.json() == {"error": "cross-site request refused"}
        # Same request without a foreign Origin reaches the auth check instead.
        assert httpx.post(url, json={}).status_code == 401
    finally:
        server.shutdown()
        server.server_close()


def test_a_browser_hanging_up_mid_response_is_not_an_error():
    """Navigating away while a request is in flight closes the socket; the
    server should shrug, not print a traceback."""
    from jobs_agent.web.handler import Handler

    class Gone:
        def write(self, data):
            raise BrokenPipeError(32, "Broken pipe")

    h = Handler.__new__(Handler)
    h.wfile = Gone()
    h.request_version = "HTTP/1.1"
    h.requestline = "GET / HTTP/1.1"
    h.command = "GET"
    h._headers_buffer = []
    h.send_response = lambda *a, **k: None
    h.send_header = lambda *a, **k: None
    h.end_headers = lambda: None
    h._send(b"hello", "text/plain")   # must not raise


# -- email-confirmation redirect -------------------------------------------

from jobs_agent.web.handler import confirm_redirect_url  # noqa: E402


def test_confirmation_link_returns_to_this_deployments_login_page(monkeypatch):
    monkeypatch.delenv("APP_URL", raising=False)
    assert confirm_redirect_url(
        {"Host": "job-apps-agent.vercel.app", "X-Forwarded-Proto": "https"}
    ) == "https://job-apps-agent.vercel.app/login"
    assert confirm_redirect_url({"Host": "127.0.0.1:8765"}) == "http://127.0.0.1:8765/login"


def test_app_url_overrides_the_request_host(monkeypatch):
    monkeypatch.setenv("APP_URL", "https://job-apps-agent.vercel.app/")
    assert confirm_redirect_url({"Host": "evil.example"}) == \
        "https://job-apps-agent.vercel.app/login"


def test_sign_up_passes_the_redirect_to_supabase(monkeypatch):
    from jobs_agent.web import auth

    seen = {}

    class Reply:
        status_code = 200

        def json(self):
            return {"id": "u1"}  # confirmation required: no session yet

    def fake_post(url, **kwargs):
        seen["url"], seen["params"] = url, kwargs.get("params")
        return Reply()

    monkeypatch.setattr(auth.httpx, "post", fake_post)
    monkeypatch.setattr(auth, "supabase_url", lambda: "https://x.supabase.co")
    monkeypatch.setattr(auth, "supabase_api_key", lambda: "k")
    assert auth.sign_up("a@b.c", "pw", redirect_to="https://app.example/login") is None
    assert seen["url"] == "https://x.supabase.co/auth/v1/signup"
    assert seen["params"] == {"redirect_to": "https://app.example/login"}


# -- verified-token cache ---------------------------------------------------------

def _counting_get_user(monkeypatch, user=None):
    calls = []

    def fake(token):
        calls.append(token)
        return user
    monkeypatch.setattr(auth, "_fetch_user", fake)
    auth._VERIFIED.clear()
    return calls


def test_a_verified_token_is_not_rechecked_on_every_request(monkeypatch):
    calls = _counting_get_user(monkeypatch, {"id": "u1", "email": "a@b.c"})
    for _ in range(3):
        assert auth.resolve({auth.ACCESS_COOKIE: "tok"}).user_id == "u1"
    assert calls == ["tok"]


def test_a_cached_token_is_rechecked_once_it_goes_stale(monkeypatch):
    calls = _counting_get_user(monkeypatch, {"id": "u1", "email": ""})
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    auth.resolve({auth.ACCESS_COOKIE: "tok"})
    now[0] += auth.VERIFIED_TTL_SECONDS + 1
    auth.resolve({auth.ACCESS_COOKIE: "tok"})
    assert calls == ["tok", "tok"]


def test_a_rejected_token_is_not_cached(monkeypatch):
    calls = _counting_get_user(monkeypatch, None)
    assert auth.resolve({auth.ACCESS_COOKIE: "bad"}) is None
    assert auth.resolve({auth.ACCESS_COOKIE: "bad"}) is None
    assert calls == ["bad", "bad"]


def test_signing_out_forgets_the_token(monkeypatch):
    calls = _counting_get_user(monkeypatch, {"id": "u1", "email": ""})
    monkeypatch.setattr(auth.httpx, "post", lambda *a, **k: None)
    monkeypatch.setattr(auth, "supabase_url", lambda: "https://x.supabase.co")
    monkeypatch.setattr(auth, "supabase_api_key", lambda: "k")
    auth.resolve({auth.ACCESS_COOKIE: "tok"})
    auth.sign_out("tok")
    auth.resolve({auth.ACCESS_COOKIE: "tok"})
    assert calls == ["tok", "tok"]
