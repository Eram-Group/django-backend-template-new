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

import structlog
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from ninja import Router

from apps.payments import services
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.exceptions import WebhookRejectedError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways.base import CardTokenEvent
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import WebhookVerificationError

logger = structlog.get_logger(__name__)
router = Router(tags=["payments-webhooks"])


@router.post(
    "/webhooks/{gateway_name}",
    auth=None,
    response=dict[str, bool],
    summary="Gateway webhook (server-to-server)",
    include_in_schema=False,
)
def payment_webhook(request: HttpRequest, gateway_name: str) -> dict[str, bool]:
    # A rejected webhook is money the provider took and we did not record -
    # ERROR so it reaches Sentry instead of hiding in the access log (a wrong
    # HMAC secret on a fresh deployment rejects every callback this way).
    # Reasons are fixed strings - never the posted signature or body.
    try:
        gateway = gateway_by_name(gateway_name)
    except PaymentGatewayUnavailableError as exc:
        logger.error(  # noqa: TRY400 - the reason string is the signal
            "payment_webhook_rejected",
            gateway=gateway_name,
            reason="unknown or unconfigured gateway",
        )
        raise WebhookRejectedError(str(_("Unknown payment gateway."))) from exc
    try:
        event = gateway.parse_webhook(
            headers=request.headers, params=request.GET, body=request.body
        )
    except WebhookVerificationError as exc:
        logger.error(  # noqa: TRY400 - a traceback into hmac.compare_digest adds nothing
            "payment_webhook_rejected", gateway=gateway_name, reason=str(exc)
        )
        raise WebhookRejectedError(str(_("Webhook verification failed."))) from exc
    except GatewayResponseError as exc:
        # Signed, but not the documented shape: retrying cannot fix it, so
        # the provider gets a 400 and Sentry gets the traceback.
        logger.exception(
            "payment_webhook_rejected", gateway=gateway_name, reason="malformed payload"
        )
        raise WebhookRejectedError(str(_("Webhook payload is malformed."))) from exc
    if isinstance(event, CardTokenEvent):
        services.saved_card_store_from_event(gateway_name=gateway_name, event=event)
        return {"ok": True}
    try:
        services.payment_apply_gateway_event(gateway_name=gateway_name, event=event)
    except PaymentNotFoundError:
        logger.warning(
            "payment_webhook_unknown_payment",
            gateway=gateway_name,
            reference=event.reference,
        )
        raise
    return {"ok": True}
