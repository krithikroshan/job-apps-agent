"""The server fetches URLs users type in, so it must never be pointed at
itself, the cloud metadata service, or anything else private."""

import pytest

from jobs_agent.careers.net import UnsafeURL, check_url


@pytest.mark.parametrize("url", [
    "https://boards.greenhouse.io/monzo",
    "https://careers.example.co.uk/graduates?x=1",
])
def test_public_https_urls_pass(url, monkeypatch):
    monkeypatch.setattr("jobs_agent.careers.net._resolve", lambda host: ["93.184.216.34"])
    assert check_url(url) == url


@pytest.mark.parametrize("url,why", [
    ("ftp://example.com/jobs", "http"),
    ("javascript:alert(1)", "http"),
    ("https://user:pw@example.com/", "credentials"),
    ("https://example.com:8443/jobs", "port"),
    ("https://localhost/jobs", "address"),
    ("http://127.0.0.1/", "address"),
    ("http://169.254.169.254/latest/meta-data/", "address"),
    ("http://[::1]/", "address"),
    ("http://10.0.0.5/", "address"),
    ("https://" + "a" * 300 + ".com/", "long"),
])
def test_unsafe_urls_are_refused(url, why):
    with pytest.raises(UnsafeURL, match=why):
        check_url(url)


def test_a_hostname_that_resolves_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr("jobs_agent.careers.net._resolve", lambda host: ["192.168.1.10"])
    with pytest.raises(UnsafeURL, match="address"):
        check_url("https://innocent-looking.example.com/careers")


# --- fetching: SSRF guard on redirects, per-host pacing ----------------------

import asyncio  # noqa: E402

import httpx  # noqa: E402

from jobs_agent.careers import net  # noqa: E402


def _client(pages, seen=None, guarded=False):
    def handler(request):
        if seen is not None:
            seen.append(str(request.url))
        return pages.get(str(request.url), httpx.Response(404))
    transport = httpx.MockTransport(handler)
    return net.make_client(transport) if guarded else httpx.AsyncClient(transport=transport)


@pytest.fixture
def waits(monkeypatch):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)
    monkeypatch.setattr(net, "_sleep", fake_sleep)
    return recorded


def test_a_redirect_to_a_private_address_is_refused(monkeypatch, waits):
    monkeypatch.setattr(net, "_resolve", lambda host: ["93.184.216.34"])
    pages = {"https://jobs.example/careers": httpx.Response(
        302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})}

    async def go():
        async with _client(pages, guarded=True) as client:
            return await client.get("https://jobs.example/careers")
    with pytest.raises(UnsafeURL, match="address"):
        asyncio.run(go())


def test_make_client_identifies_itself():
    client = net.make_client()
    assert client.headers["User-Agent"] == net.USER_AGENT
    assert client.follow_redirects
    asyncio.run(client.aclose())


def test_fetch_text_never_asks_for_robots_txt(waits):
    seen = []
    pages = {"https://x.example/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /\n"),
             "https://x.example/private/1": httpx.Response(200, text="P")}

    async def go():
        async with _client(pages, seen) as client:
            return await net.fetch_text(client, "https://x.example/private/1")
    assert asyncio.run(go()) == "P"
    assert seen == ["https://x.example/private/1"]


def test_requests_to_one_host_are_spaced_out(waits):
    pages = {"https://x.example/a": httpx.Response(200, text="A"),
             "https://x.example/b": httpx.Response(200, text="B"),
             "https://y.example/a": httpx.Response(200, text="A")}

    async def go():
        async with _client(pages) as client:
            await net.fetch_text(client, "https://x.example/a")
            await net.fetch_text(client, "https://x.example/b")  # same host: waits its turn
            await net.fetch_text(client, "https://y.example/a")  # new host: no wait
    asyncio.run(go())
    assert len(waits) == 1
    assert 0 < waits[0] <= net.MIN_INTERVAL


def test_fetch_json_calls_the_api_directly(waits):
    seen = []
    pages = {"https://api.example/v1/jobs": httpx.Response(200, json={"jobs": [1]})}

    async def go():
        async with _client(pages, seen) as client:
            return await net.fetch_json(client, "GET", "https://api.example/v1/jobs")
    assert asyncio.run(go()) == {"jobs": [1]}
    assert seen == ["https://api.example/v1/jobs"]


# --- deadline, body caps, polite retry ----------------------------------------

def _slow_client(chunks, delay):
    """A client whose response trickles ``chunks`` with ``delay`` between them."""
    async def trickle():
        for chunk in chunks:
            await asyncio.sleep(delay)
            yield chunk

    async def handler(request):
        return httpx.Response(200, content=trickle())
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("call", [
    lambda client: net.fetch_text(client, "https://slow.example/"),
    lambda client: net.fetch_json(client, "GET", "https://slow.example/api"),
])
def test_a_body_that_trickles_in_hits_the_total_deadline(monkeypatch, waits, call):
    # Every chunk arrives well inside httpx's per-read timeout, but the whole
    # body never finishes: only a total deadline stops a drip-feeding server.
    monkeypatch.setattr(net, "FETCH_DEADLINE_SECONDS", 0.1)

    async def go():
        async with _slow_client([b"x"] * 1000, 0.01) as client:
            return await call(client)
    with pytest.raises(httpx.TimeoutException, match="deadline"):
        asyncio.run(go())


def test_fetch_json_refuses_a_body_over_the_json_cap(monkeypatch, waits):
    monkeypatch.setattr(net, "MAX_JSON_BYTES", 10)
    pages = {"https://api.example/big": httpx.Response(200, json={"jobs": list(range(100))})}

    async def go():
        async with _client(pages) as client:
            return await net.fetch_json(client, "GET", "https://api.example/big")
    with pytest.raises(net.ResponseTooLarge):
        asyncio.run(go())


def test_fetch_text_truncates_at_the_body_cap(monkeypatch, waits):
    monkeypatch.setattr(net, "MAX_BODY_BYTES", 5)
    pages = {"https://x.example/": httpx.Response(200, text="abcdefghij")}

    async def go():
        async with _client(pages) as client:
            return await net.fetch_text(client, "https://x.example/")
    assert asyncio.run(go()) == "abcde"


def _flaky(statuses, retry_after="2"):
    """A client answering with each status in turn (200s carry JSON)."""
    seen = []

    def handler(request):
        status = statuses[min(len(seen), len(statuses) - 1)]
        seen.append(str(request.url))
        headers = {"Retry-After": retry_after} if retry_after is not None else {}
        if status == 200:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(status, headers=headers)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


@pytest.mark.parametrize("fetch", [
    lambda c: net.fetch_json(c, "GET", "https://api.example/x"),
    lambda c: net.fetch_text(c, "https://api.example/x"),
])
@pytest.mark.parametrize("status", [429, 503])
def test_one_polite_retry_after_a_short_retry_after(waits, status, fetch):
    client, seen = _flaky([status, 200])

    async def go():
        async with client:
            return await fetch(client)
    result = asyncio.run(go())
    assert result in ({"ok": True}, '{"ok":true}')
    assert len(seen) == 2
    assert 2.0 in waits


def test_only_one_retry_then_the_error_is_raised(waits):
    client, seen = _flaky([429, 429, 200])

    async def go():
        async with client:
            return await net.fetch_json(client, "GET", "https://api.example/x")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert len(seen) == 2


@pytest.mark.parametrize("retry_after", ["60", "Wed, 21 Oct 2026 07:28:00 GMT"])
def test_a_long_or_unparseable_retry_after_is_not_waited_for(waits, retry_after):
    client, seen = _flaky([503, 200], retry_after=retry_after)

    async def go():
        async with client:
            return await net.fetch_json(client, "GET", "https://api.example/x")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert len(seen) == 1


def test_other_errors_are_not_retried(waits):
    client, seen = _flaky([500, 200])

    async def go():
        async with client:
            return await net.fetch_text(client, "https://api.example/x")
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())
    assert len(seen) == 1
