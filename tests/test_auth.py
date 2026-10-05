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
