"""Delivery-status webhook receiver - unauthenticated but SIGNATURE-VERIFIED.

``auth=None`` mirrors the payments webhook: providers cannot log in;
authenticity comes from the HMAC check (constant-time compare). A bad
signature is a 400 envelope so the provider retries/alerts; an UNKNOWN
message id still acks 200 - this is telemetry, not money, and a Meta retry
storm must not build. FCM has no delivery webhooks (push is terminal at
SENT); SMS DLRs land here later once provider formats are verified.

Nothing constructible from outside may 500 here: Meta retries 5xx with
backoff for days, so every input surprise - a non-ASCII signature header, a
JSON body that is not an object, ``entry``/``changes``/``value``/``statuses``
of the wrong type - is either a 400 (before the signature passes) or a
200 no-op (after it).
"""

import hashlib
import hmac
import json
from typing import Any

import structlog
from django.conf import settings
from django.http import HttpRequest
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from ninja import Router

from apps.notifications import services
from apps.notifications.constants import DeliveryStatus
from apps.notifications.exceptions import NotificationWebhookRejectedError

logger = structlog.get_logger(__name__)
router = Router(tags=["notifications-webhooks"])

_META_STATUS_MAP = {
    "sent": DeliveryStatus.SENT,
    "delivered": DeliveryStatus.DELIVERED,
    "read": DeliveryStatus.READ,
    "failed": DeliveryStatus.FAILED,
}


@router.get(
    "/webhooks/whatsapp",
    auth=None,
    throttle=[],  # token-verified handshake, Meta-driven cadence
    summary="Meta webhook verification handshake",
    include_in_schema=False,
)
def whatsapp_webhook_verify(request: HttpRequest) -> HttpResponse:
    """Meta subscribes by echoing hub.challenge back - iff the verify token
    matches ours; an unset token rejects (fail closed)."""
    verify_token = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
    if verify_token is None:
        raise _rejected(reason="verify token not configured")
    try:
        received = request.GET["hub.verify_token"]
        challenge = request.GET["hub.challenge"]
    except KeyError:
        raise _rejected(reason="handshake parameters missing") from None
    if not hmac.compare_digest(received.encode(), verify_token.encode()):
        raise _rejected(reason="verify token mismatch")
    return HttpResponse(challenge)


@router.post(
    "/webhooks/whatsapp",
    auth=None,
    throttle=[],  # HMAC-verified; a Meta status burst must never be dropped
    response=dict[str, bool],
    summary="WhatsApp delivery statuses (server-to-server)",
    include_in_schema=False,
)
def whatsapp_webhook(request: HttpRequest) -> dict[str, bool]:
    _verify_signature(request)
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        raise _rejected(
            reason="body is not valid JSON",
            message=str(_("Webhook payload is not valid JSON.")),
        ) from exc
    if not isinstance(payload, dict):
        raise _rejected(
            reason="body is not a JSON object",
            message=str(_("Webhook payload is not valid JSON.")),
        )
    for status_item in _iter_statuses(payload):
        status = status_item.get("status")
        message_id = status_item.get("id")
        if not isinstance(message_id, str) or not message_id:
            continue
        mapped = _META_STATUS_MAP.get(status) if isinstance(status, str) else None
        if mapped is None:
            # Meta may add statuses (e.g. "warning"); not a rejection - they
            # would retry - but a real signal that the map is out of date.
            logger.warning(
                "notification_webhook_status_unknown",
                provider="whatsapp",
                status=str(status)[:50],
            )
            continue
        services.delivery_update_status(
            provider="whatsapp",
            provider_message_id=message_id,
            status=mapped,
            detail=_error_detail(status_item),
        )
    return {"ok": True}


def _rejected(
    *, reason: str, message: str | None = None
) -> NotificationWebhookRejectedError:
    """Log at ERROR, then hand back the 400 to raise.

    A rejected webhook is a provider receipt we did not record - ERROR so a
    wrong ``WHATSAPP_APP_SECRET`` on a fresh deployment reaches Sentry instead
    of hiding in the access log. ``reason`` is one of our fixed strings, never
    the posted signature or body.
    """
    logger.error("notification_webhook_rejected", provider="whatsapp", reason=reason)
    return NotificationWebhookRejectedError(
        message or str(_("Webhook verification failed."))
    )


def _verify_signature(request: HttpRequest) -> None:
    """X-Hub-Signature-256 = HMAC-SHA256(app secret, raw body).

    Both sides are compared as bytes: ``hmac.compare_digest`` raises TypeError
    on a non-ASCII ``str``, and the header is attacker-controlled.
    """
    secret = settings.WHATSAPP_APP_SECRET
    if secret is None:  # unset config fails closed, never open
        raise _rejected(reason="app secret not configured")
    expected = (
        "sha256="
        + hmac.new(
            secret.get_secret_value().encode(), request.body, hashlib.sha256
        ).hexdigest()
    )
    received = request.headers.get("X-Hub-Signature-256", "")
    if not hmac.compare_digest(received.encode(), expected.encode()):
        raise _rejected(reason="signature mismatch")


def _iter_statuses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """entry[].changes[].value.statuses[] - tolerant of missing AND mistyped keys."""
    found: list[dict[str, Any]] = []
    for entry in _as_list(payload.get("entry")):
        if not isinstance(entry, dict):
            continue
        for change in _as_list(entry.get("changes")):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            found.extend(
                status_item
                for status_item in _as_list(value.get("statuses"))
                if isinstance(status_item, dict)
            )
    return found


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _error_detail(status_item: dict[str, Any]) -> str:
    """``errors[0].title`` - Meta's human-readable error field (``code`` is
    the numeric id, ``message`` its legacy duplicate). Empty when absent or
    mistyped: the row still moves to FAILED, only the detail is lost."""
    errors = _as_list(status_item.get("errors"))
    if not errors or not isinstance(errors[0], dict):
        return ""
    title = errors[0].get("title")
    return title if isinstance(title, str) else ""
