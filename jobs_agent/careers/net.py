"""Safe, polite HTTP for fetching careers sites.

Unlike the Reed and Adzuna adapters, these requests go to URLs that users
type in. That makes the server an open proxy unless every URL is checked:
without ``check_url`` a user could point it at ``localhost``, the cloud
metadata service (169.254.169.254) or a private network address and read
the response back through the job queue (SSRF). The check runs on every
request a ``make_client`` client sends, redirects included, because a
public URL can redirect to a private one.

We still go easy on other people's websites: requests identify themselves
with a descriptive User-Agent, and no host gets more than one request per
second. A 429 or 503 with a short Retry-After gets exactly one retry after
the wait it asks for; anything longer is the site saying "not now", and we
take it at its word. robots.txt is deliberately not consulted — the app reads only the
careers pages a user points it at, not a crawl.

Every fetch also has a total deadline. httpx's timeout applies to each
read, so a server that drip-feeds one byte every few seconds would hold a
worker forever without it; overrunning it raises ``httpx.ReadTimeout``, so
callers handle it like any other timeout.

Known gap: the address check resolves DNS separately from the connection
httpx makes, so a hostile DNS server could answer differently the second
time (DNS rebinding). Closing that needs a custom transport that connects
to the vetted IP; the check still blocks every straightforward attempt.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import weakref
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

USER_AGENT = "JobsAgent/0.1 (graduate job search; +https://github.com/krithikroshan/job-apps-agent)"

TIMEOUT_SECONDS = 20.0
MIN_INTERVAL = 1.0           # seconds between requests to one host
FETCH_DEADLINE_SECONDS = 15.0  # whole request + body, per attempt
MAX_BODY_BYTES = 5_000_000   # a careers page bigger than this is not worth reading
MAX_JSON_BYTES = 10_000_000  # whole-board ATS feeds are big; truncated JSON is useless
RETRY_STATUSES = (429, 503)  # "slow down" / "try again shortly"
MAX_RETRY_AFTER = 5.0        # longest Retry-After we will wait out (seconds)
_MAX_HOSTNAME = 253          # the DNS limit for a full domain name
_ALLOWED_PORTS = (None, 80, 443)
_LOCAL_NAMES = ("localhost", "localhost.localdomain")

# Indirection so tests can stub the clock without slowing down.
_sleep = asyncio.sleep

log = logging.getLogger(__name__)


class UnsafeURL(ValueError):
    """A URL the server must not fetch (private address, odd scheme, ...)."""


class ResponseTooLarge(ValueError):
    """A JSON response over MAX_JSON_BYTES: cut short, it can't be parsed."""


# --- URL safety -------------------------------------------------------------

def _resolve(host: str) -> list[str]:
    """Every address ``host`` resolves to (module-level so tests can stub DNS)."""
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # drop IPv6 zone id
    except ValueError:
        return False
    # ::ffff:127.0.0.1 is loopback in disguise; judge the embedded IPv4.
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_global


def _literal_ip(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return None


def _check_host(host: str) -> None:
    if not host:
        raise UnsafeURL("URL has no host address")
    if len(host) > _MAX_HOSTNAME:
        raise UnsafeURL("hostname is too long")
    lowered = host.lower().rstrip(".")
    if lowered in _LOCAL_NAMES or lowered.endswith(".localhost"):
        raise UnsafeURL(f"{host} is a local address")
    literal = _literal_ip(host)
    if literal is not None:
        addresses = [literal]
    else:
        try:
            addresses = _resolve(host)
        except (OSError, UnicodeError) as exc:
            raise UnsafeURL(f"could not resolve the address of {host}") from exc
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UnsafeURL(f"{host} resolves to a private or reserved address")


def check_url(url: str) -> str:
    """Return ``url`` unchanged if it is safe to fetch, else raise UnsafeURL.

    Safe means: http(s) only, no embedded credentials, the default port, and
    a host whose every address is publicly routable.
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        raise UnsafeURL("only http and https URLs can be fetched")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("URLs with embedded credentials are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeURL("URL has an invalid port") from exc
    if port not in _ALLOWED_PORTS:
        raise UnsafeURL(f"port {port} is not allowed; use the standard web port")
    _check_host(parts.hostname or "")
    return url


# --- client and per-client politeness state ---------------------------------

@dataclass
class _HostState:
    """Per-client bookkeeping: request pacing by host."""
    pace_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    last_request: dict[str, float] = field(default_factory=dict)


# Keyed weakly so state disappears with the client that owns it.
_STATE: "weakref.WeakKeyDictionary[httpx.AsyncClient, _HostState]" = weakref.WeakKeyDictionary()


def _state(client: httpx.AsyncClient) -> _HostState:
    state = _STATE.get(client)
    if state is None:
        state = _HostState()
        _STATE[client] = state
    return state


async def _guard_request(request: httpx.Request) -> None:
    # DNS lookup blocks, so keep it off the event loop.
    await asyncio.to_thread(check_url, str(request.url))


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """An AsyncClient that refuses unsafe URLs on every hop, redirects included.

    ``transport`` exists for tests (httpx.MockTransport); production omits it.
    """
    return httpx.AsyncClient(
        transport=transport,
        timeout=TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        event_hooks={"request": [_guard_request]},
    )


async def _wait_turn(client: httpx.AsyncClient, host: str) -> None:
    """Block until ``host`` has had no request from this client for MIN_INTERVAL."""
    state = _state(client)
    lock = state.pace_locks.setdefault(host, asyncio.Lock())
    loop = asyncio.get_running_loop()
    async with lock:
        last = state.last_request.get(host)
        if last is not None:
            wait = MIN_INTERVAL - (loop.time() - last)
            if wait > 0:
                await _sleep(wait)
        state.last_request[host] = loop.time()


async def _read_capped(client: httpx.AsyncClient, method: str, url: str, cap: int,
                       kwargs: dict[str, Any]) -> tuple[httpx.Response, bytes, str]:
    """One request: (response, body, encoding). The body stops just past ``cap``
    bytes, so callers can tell "exactly cap" from "too big". Error statuses
    come back unread for the caller to judge."""
    async with client.stream(method, url, **kwargs) as response:
        if response.is_error:
            return response, b"", "utf-8"
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                break
        return response, b"".join(chunks), response.encoding or "utf-8"


async def _attempt(client: httpx.AsyncClient, method: str, url: str, cap: int,
                   kwargs: dict[str, Any]) -> tuple[httpx.Response, bytes, str]:
    """``_read_capped`` under FETCH_DEADLINE_SECONDS, overrun -> httpx.ReadTimeout."""
    try:
        # wait_for rather than asyncio.timeout(): the app supports Python 3.9.
        return await asyncio.wait_for(_read_capped(client, method, url, cap, kwargs),
                                      FETCH_DEADLINE_SECONDS)
    except asyncio.TimeoutError as exc:
        raise httpx.ReadTimeout(
            f"{url} missed the {FETCH_DEADLINE_SECONDS:g}s deadline",
            request=httpx.Request(method, url)) from exc


def _retry_delay(response: httpx.Response) -> float | None:
    """Seconds to wait before the one retry, or None to give up now.

    A missing Retry-After gets the normal per-host interval; an HTTP-date or
    anything over MAX_RETRY_AFTER is not worth holding a worker for.
    """
    if response.status_code not in RETRY_STATUSES:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return MIN_INTERVAL
    try:
        delay = float(raw)
    except ValueError:
        return None
    return delay if 0 <= delay <= MAX_RETRY_AFTER else None


async def _fetch(client: httpx.AsyncClient, method: str, url: str, cap: int,
                 **kwargs: Any) -> tuple[bytes, str]:
    """(body, encoding) for a paced, deadlined request with at most one retry.

    Worst case per call: two deadlines plus MAX_RETRY_AFTER (35s by default),
    not counting the wait for this host's turn.
    """
    host = urlsplit(url).hostname or ""
    for attempt in range(2):
        await _wait_turn(client, host)
        response, body, encoding = await _attempt(client, method, url, cap, kwargs)
        delay = _retry_delay(response) if attempt == 0 else None
        if delay is None:
            response.raise_for_status()
            return body, encoding
        log.info("%s answered %s; retrying once in %gs", url, response.status_code, delay)
        await _sleep(delay)
    raise AssertionError("unreachable: the second attempt always returns or raises")


async def _get_body(client: httpx.AsyncClient, url: str) -> str:
    """GET ``url`` (paced, deadlined) and return its text, capped at MAX_BODY_BYTES."""
    body, encoding = await _fetch(client, "GET", url, MAX_BODY_BYTES)
    return body[:MAX_BODY_BYTES].decode(encoding, errors="replace")


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Fetch a web page politely: paced per host, raising on HTTP errors."""
    return await _get_body(client, url)


async def fetch_json(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> Any:
    """Call a documented public JSON API: paced, deadlined, size-capped.

    Raises ResponseTooLarge (a ValueError) past MAX_JSON_BYTES, and ValueError
    for a body that isn't JSON, as ``response.json()`` would.
    """
    body, _encoding = await _fetch(client, method, url, MAX_JSON_BYTES, **kwargs)
    if len(body) > MAX_JSON_BYTES:
        raise ResponseTooLarge(f"{url} returned more than {MAX_JSON_BYTES} bytes")
    return json.loads(body)  # bytes: json detects UTF-8/16/32 itself
