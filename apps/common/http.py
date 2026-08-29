"""Outbound HTTP kernel - every external-service call goes through here.

``request_json`` wraps one httpx request with an explicit timeout, a typed
retry policy (stamina), structured logging, and a small error taxonomy.
Both the timeout and the retry policy are stated by every caller - the
policy IS the caller's idempotency posture:

- ``"transient"`` retries transport errors and 429/5xx responses - for calls
  where a duplicate beats a drop (status polls).
- ``"connect-only"`` retries only errors raised before the request hit the
  wire - for non-idempotent POSTs (payment charges) unless the provider
  accepts an idempotency key.
- ``"none"`` sends exactly once.

Failures surface as ``OutboundError`` subclasses. These are deliberately NOT
``ApplicationError``: inside a task the exception fails the task loudly
(FAILED row + Sentry); API-path callers catch and wrap into their own domain
error. A 2xx response with an error payload is the caller's job to detect -
each provider module owns its success predicate.

Request bodies are never logged (OTP codes, PII); response bodies travel only
inside raised errors, truncated.
"""

import time
from collections.abc import Callable
from collections.abc import Mapping
from typing import Any
from typing import Literal

import httpx
import stamina
import structlog

logger = structlog.get_logger(__name__)

#: The timeout every provider call states unless it has a reason of its own.
PROVIDER_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

# One pooled client per process so TLS handshakes and connections are reused
# across provider calls. httpx.Client is thread-safe and lazy about
# connecting (nothing happens at import; gunicorn forks before the first
# request); the timeout is passed per request.
_client = httpx.Client()

type RetryPolicy = Literal["transient", "connect-only", "none"]

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_ATTEMPTS = 3
_RETRY_WINDOW_SECONDS = 30.0
_BODY_TRUNCATE_CHARS = 500


class OutboundError(Exception):
    """Base for outbound-HTTP failures (after retries were exhausted)."""

    def __init__(
        self, *, service: str, detail: str, status_code: int | None, body: str
    ) -> None:
        self.service = service
        self.detail = detail
        self.status_code = status_code
        self.body = body[:_BODY_TRUNCATE_CHARS]
        super().__init__(f"{service}: {detail}")


class OutboundTransportError(OutboundError):
    """DNS/connect/timeout failure - the response never arrived.

    ``request_sent`` is False only when the failure provably happened before
    the request hit the wire (DNS/connect phase). True means the outcome is
    UNKNOWN - a read timeout may have been processed by the provider -
    so callers that trigger non-idempotent effects (payment refunds) must
    not auto-revert on it.
    """

    def __init__(self, *, service: str, detail: str, request_sent: bool) -> None:
        self.request_sent = request_sent
        super().__init__(service=service, detail=detail, status_code=None, body="")


class OutboundStatusError(OutboundError):
    """The service answered with a non-2xx status."""


def _before_the_wire(exc: Exception) -> bool:
    """Connect-phase failures are the only ones where the request provably
    never went out."""
    return isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)


def _transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _RETRYABLE_STATUSES
    )


def _never(exc: Exception) -> bool:
    return False


_RETRY_ON: dict[RetryPolicy, Callable[[Exception], bool]] = {
    "transient": _transient,
    "connect-only": _before_the_wire,
    "none": _never,
}


def request_json(
    *,
    service: str,
    method: str,
    url: str,
    json: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout,
    retry: RetryPolicy,
) -> httpx.Response:
    """Send one JSON request and return the 2xx response.

    Raises ``OutboundStatusError`` on a non-2xx answer and
    ``OutboundTransportError`` when no answer arrived, in both cases only
    after the retry policy is exhausted.
    """
    started = time.monotonic()
    attempts = 0

    def send_once() -> httpx.Response:
        nonlocal attempts
        attempts += 1
        response = _client.request(
            method,
            url,
            json=json,
            headers=dict(headers) if headers else None,
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    # One retry loop for all three policies - "none" is the predicate that
    # never retries.
    send = stamina.retry(
        on=_RETRY_ON[retry], attempts=_ATTEMPTS, timeout=_RETRY_WINDOW_SECONDS
    )(send_once)

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        response = send()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "outbound_request_failed",
            service=service,
            method=method,
            url=url,
            status_code=exc.response.status_code,
            elapsed_ms=elapsed_ms(),
            attempts=attempts,
        )
        raise OutboundStatusError(
            service=service,
            detail=f"{method} {url} -> {exc.response.status_code}",
            status_code=exc.response.status_code,
            body=exc.response.text,
        ) from exc
    except httpx.TransportError as exc:
        logger.warning(
            "outbound_request_failed",
            service=service,
            method=method,
            url=url,
            error=type(exc).__name__,
            elapsed_ms=elapsed_ms(),
            attempts=attempts,
        )
        raise OutboundTransportError(
            service=service,
            detail=f"{method} {url} -> {type(exc).__name__}: {exc}",
            request_sent=not _before_the_wire(exc),
        ) from exc

    logger.info(
        "outbound_request_ok",
        service=service,
        method=method,
        url=url,
        status_code=response.status_code,
        elapsed_ms=elapsed_ms(),
        attempts=attempts,
    )
    return response
