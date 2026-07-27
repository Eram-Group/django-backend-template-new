"""Delivery-status webhook receiver - unauthenticated but SIGNATURE-VERIFIED.

``auth=None`` mirrors the payments webhook: providers cannot log in;
authenticity comes from the HMAC check (constant-time compare). A bad
signature is a 400 envelope so the provider retries/alerts; an UNKNOWN
message id still acks 200 - this is telemetry, not money, and a Meta retry
storm must not build. FCM has no delivery webhooks (push is terminal at
SENT); SMS DLRs land here later once provider formats are verified.
"""

import hashlib
import hmac
import json
from typing import Any

from django.conf import settings
from django.http import HttpRequest
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from ninja import Router

from apps.notifications import services
from apps.notifications.constants import DeliveryStatus
from apps.notifications.exceptions import NotificationWebhookRejectedError

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
    summary="Meta webhook verification handshake",
    include_in_schema=False,
)
def whatsapp_webhook_verify(request: HttpRequest) -> HttpResponse:
    """Meta subscribes by echoing hub.challenge back - iff the verify token
    matches ours; an unset token rejects (fail closed)."""
    verify_token = settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN
    if verify_token is None or not hmac.compare_digest(
        request.GET.get("hub.verify_token", ""), verify_token
    ):
        raise NotificationWebhookRejectedError(str(_("Webhook verification failed.")))
    return HttpResponse(request.GET.get("hub.challenge", ""))


@router.post(
    "/webhooks/whatsapp",
    auth=None,
    response=dict[str, bool],
    summary="WhatsApp delivery statuses (server-to-server)",
    include_in_schema=False,
)
def whatsapp_webhook(request: HttpRequest) -> dict[str, bool]:
    _verify_signature(request)
    try:
        payload = json.loads(request.body)
    except ValueError as exc:
        raise NotificationWebhookRejectedError(
            str(_("Webhook payload is not valid JSON."))
        ) from exc
    for status_item in _iter_statuses(payload):
        mapped = _META_STATUS_MAP.get(status_item.get("status", ""))
        message_id = status_item.get("id", "")
        if mapped is None or not message_id:
            continue
        services.delivery_update_status(
            provider="whatsapp",
            provider_message_id=message_id,
            status=mapped,
            detail=_error_detail(status_item),
        )
    return {"ok": True}


def _verify_signature(request: HttpRequest) -> None:
    """X-Hub-Signature-256 = HMAC-SHA256(app secret, raw body)."""
    secret = settings.WHATSAPP_APP_SECRET
    if secret is None:  # unset config fails closed, never open
        raise NotificationWebhookRejectedError(str(_("Webhook verification failed.")))
    expected = (
        "sha256="
        + hmac.new(
            secret.get_secret_value().encode(), request.body, hashlib.sha256
        ).hexdigest()
    )
    received = request.headers.get("X-Hub-Signature-256", "")
    if not hmac.compare_digest(received, expected):
        raise NotificationWebhookRejectedError(str(_("Webhook verification failed.")))


def _iter_statuses(payload: Any) -> list[dict[str, Any]]:
    """entry[].changes[].value.statuses[] - tolerant of missing keys."""
    if not isinstance(payload, dict):
        return []
    return [
        status_item
        for entry in payload.get("entry", [])
        if isinstance(entry, dict)
        for change in entry.get("changes", [])
        if isinstance(change, dict)
        for status_item in change.get("value", {}).get("statuses", [])
        if isinstance(status_item, dict)
    ]


def _error_detail(status_item: dict[str, Any]) -> str:
    errors = status_item.get("errors") or []
    if not errors or not isinstance(errors[0], dict):
        return ""
    first = errors[0]
    return str(first.get("title") or first.get("message") or first.get("code") or "")
