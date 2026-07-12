"""Outbound HTTP kernel - every external-service call goes through here.

``request_json`` wraps one httpx request with explicit timeouts, a typed
retry policy (stamina), structured logging, and a small error taxonomy:

- ``"transient"`` retries transport errors and 429/5xx responses - for calls
  where a duplicate beats a drop (SMS, status polls).
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
from collections.abc import Mapping
from typing import Any
from typing import Literal

import httpx
import stamina
import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

type RetryPolicy = Literal["transient", "connect-only", "none"]

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_ATTEMPTS = 3
_RETRY_WINDOW_SECONDS = 30.0
_BODY_TRUNCATE_CHARS = 500


class OutboundError(Exception):
    """Base for outbound-HTTP failures (after retries were exhausted)."""

    def __init__(
        self,
        *,
        service: str,
        detail: str,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        self.service = service
        self.detail = detail
        self.status_code = status_code
        self.body = body[:_BODY_TRUNCATE_CHARS] if body is not None else None
        super().__init__(f"{service}: {detail}")


class OutboundTransportError(OutboundError):
    """DNS/connect/timeout failure - the response never arrived."""


class OutboundStatusError(OutboundError):
    """The service answered with a non-2xx status."""


def _retry_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in _RETRYABLE_STATUSES
    )


def _retry_connect_only(exc: Exception) -> bool:
    return isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)


def _send_once(
    *,
    method: str,
    url: str,
    json: Mapping[str, Any] | None,
    headers: Mapping[str, str] | None,
    timeout: httpx.Timeout,
) -> httpx.Response:
    with httpx.Client(timeout=timeout) as client:
        response = client.request(
            method,
            url,
            json=json,
            headers=dict(headers) if headers else None,
        )
        response.raise_for_status()
        return response


def request_json(
    *,
    service: str,
    method: str,
    url: str,
    json: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    retry: RetryPolicy = "transient",
) -> httpx.Response:
    """Send one JSON request and return the 2xx response.

    Raises ``OutboundStatusError`` on a non-2xx answer and
    ``OutboundTransportError`` when no answer arrived, in both cases only
    after the retry policy is exhausted.
    """
    started = time.monotonic()
    attempts = 0

    def send() -> httpx.Response:
        nonlocal attempts
        if retry == "none":
            attempts = 1
            return _send_once(
                method=method, url=url, json=json, headers=headers, timeout=timeout
            )
        predicate = _retry_transient if retry == "transient" else _retry_connect_only
        for attempt in stamina.retry_context(
            on=predicate, attempts=_ATTEMPTS, timeout=_RETRY_WINDOW_SECONDS
        ):
            with attempt:
                attempts = attempt.num
                return _send_once(
                    method=method, url=url, json=json, headers=headers, timeout=timeout
                )
        raise AssertionError("unreachable: stamina always yields at least once")

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
