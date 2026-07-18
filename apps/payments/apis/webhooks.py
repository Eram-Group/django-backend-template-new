"""Gateway webhook receiver - unauthenticated but SIGNATURE-VERIFIED.

``auth=None`` is the one deliberate unauthenticated surface in the API:
gateways cannot log in. Authenticity comes from each gateway's HMAC check in
``parse_webhook`` (constant-time compare); a bad signature is a 400 envelope
and an unknown payment a 404 - both make the gateway retry/alert instead of
silently succeeding. Replays of a paid payment return 200 without
re-crediting (TERMINAL_STATUSES guard in the service).

Two event kinds arrive here: payment transitions, and standalone card-token
events (Paymob TOKEN callbacks) which carry no payment reference and are
stored against the user by billing email. Both ack with ``{"ok": true}`` -
gateways only read the status code.
"""

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from ninja import Router

from apps.payments import services
from apps.payments.exceptions import WebhookRejectedError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways.base import WebhookEventKind
from apps.payments.gateways.base import WebhookVerificationError

router = Router(tags=["payments-webhooks"])


@router.post(
    "/webhooks/{gateway_name}",
    auth=None,
    response=dict[str, bool],
    summary="Gateway webhook (server-to-server)",
    include_in_schema=False,
)
def payment_webhook(request: HttpRequest, gateway_name: str) -> dict[str, bool]:
    gateway = gateway_by_name(gateway_name)
    if gateway is None:
        raise WebhookRejectedError(str(_("Unknown payment gateway.")))
    try:
        event = gateway.parse_webhook(
            headers=request.headers, params=request.GET, body=request.body
        )
    except WebhookVerificationError as exc:
        raise WebhookRejectedError(str(_("Webhook verification failed."))) from exc
    if event.kind == WebhookEventKind.CARD_TOKEN:
        services.saved_card_store_from_event(gateway_name=gateway_name, event=event)
    else:
        services.payment_apply_gateway_event(gateway_name=gateway_name, event=event)
    return {"ok": True}
