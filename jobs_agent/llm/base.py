"""What every provider adapter shares: the prompt shape, the error type, and
one HTTP helper that turns every failure into an :class:`LLMError` whose
message is safe to show a user (status and the provider's own message —
never a key, since keys only ever travel in headers).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

#: Tests swap in an ``httpx.MockTransport``; None means the real network.
TRANSPORT: httpx.BaseTransport | None = None

#: Generous: a full cover letter from a slow model can take most of this.
TIMEOUT_SECONDS = 90

#: Overloaded (503, and Anthropic's 529) and rate-limited (429) responses
#: are usually brief, so one is retried after a short pause. Anything that
#: asks for a longer wait falls through to the user's next provider instead
#: of stalling the request.
RETRY_STATUSES = (429, 503, 529)
RETRY_DELAY_SECONDS = 2.0
MAX_RETRY_WAIT_SECONDS = 5.0
#: A retry is only worth it with at least this long left to actually answer.
MIN_RETRY_ANSWER_SECONDS = 5.0


class LLMError(RuntimeError):
    """A provider couldn't produce an answer. The message is user-safe."""


@dataclass(frozen=True)
class Message:
    role: str      # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Prompt:
    system: str
    messages: tuple[Message, ...]
    temperature: float
    max_tokens: int
    #: Ask for a bare JSON object. Providers without a JSON mode get the
    #: same prompt and may wrap the reply in a ``` fence; callers strip it.
    json_mode: bool = False
    #: ``time.monotonic()`` by which the whole answer is needed; every
    #: request (and retry) made for this prompt fits inside it. None: no limit.
    deadline: float | None = None


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    #: Server-side environment variables holding a fallback key, in order.
    env_vars: tuple[str, ...]
    default_model: Callable[[], str]
    #: The cheap, quick model for bulk work (analysing postings), whatever
    #: the user picked for letters.
    fast_model: Callable[[], str]
    #: Where a user gets a key.
    key_url: str
    complete: Callable[[str, str, Prompt], str]
    list_models: Callable[[str], list[str]]


def request_json(method: str, url: str, *, headers: dict[str, str],
                 json: dict | None = None, params: dict | None = None,
                 deadline: float | None = None) -> Any:
    """One HTTP exchange, retried once if the provider is briefly busy and
    there's time. Every failure is an :class:`LLMError`."""
    def send(client: httpx.Client) -> httpx.Response:
        return client.request(method, url, headers=headers, json=json, params=params,
                              timeout=_time_left(deadline))

    try:
        with httpx.Client(transport=TRANSPORT, timeout=_time_left(deadline)) as client:
            resp = send(client)
            wait = _retry_wait(resp)
            if wait is not None and _time_left(deadline, need=wait + MIN_RETRY_ANSWER_SECONDS) is not None:
                time.sleep(wait)
                resp = send(client)
    except httpx.TimeoutException:
        raise LLMError("timed out") from None
    except httpx.HTTPError as e:
        raise LLMError(f"couldn't connect ({type(e).__name__})") from None
    if resp.is_error:
        raise LLMError(f"{resp.status_code} {_error_message(resp)}")
    try:
        return resp.json()
    except ValueError:
        raise LLMError("returned a response that wasn't JSON") from None


def _time_left(deadline: float | None, *, need: float = 0.0) -> float | None:
    """Seconds for the next request: the standard timeout, cut to what's
    left before ``deadline``. With ``need``, None when that much isn't left
    (so the caller skips a retry); without, raises when nothing is left."""
    if deadline is None:
        return float(TIMEOUT_SECONDS)
    left = deadline - time.monotonic()
    if need:
        return left if left >= need else None
    if left <= 0:
        raise LLMError("ran out of time")
    return min(float(TIMEOUT_SECONDS), left)


def _retry_wait(resp: httpx.Response) -> float | None:
    """Seconds to wait before one retry, or None to not retry."""
    if resp.status_code not in RETRY_STATUSES:
        return None
    try:
        wait = float(resp.headers.get("Retry-After", RETRY_DELAY_SECONDS))
    except ValueError:
        wait = RETRY_DELAY_SECONDS
    wait = max(0.0, wait)
    return wait if wait <= MAX_RETRY_WAIT_SECONDS else None


def _error_message(resp: httpx.Response) -> str:
    """The provider's own explanation, trimmed. Every provider here nests it
    as ``{"error": {"message": ...}}`` or ``{"error": "..."}``."""
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        return resp.reason_phrase or ""
    message = err.get("message") if isinstance(err, dict) else err
    return str(message or resp.reason_phrase or "")[:300]


def require_text(text: Any, why: str = "") -> str:
    text = (text or "").strip() if isinstance(text, str) else ""
    if not text:
        raise LLMError(f"returned no text{f' ({why})' if why else ''}")
    return text


def truncated() -> LLMError:
    return LLMError("the reply was cut off at the length limit before it finished")


def rejects_param(e: LLMError, name: str) -> bool:
    """A 400 naming one request parameter — e.g. reasoning models only take
    their default ``temperature``; some refuse ``reasoning`` switched off."""
    msg = str(e).lower()
    return msg.startswith("400") and name in msg
