"""Gateway webhook receiver - unauthenticated but SIGNATURE-VERIFIED.

``auth=None`` is the one deliberate unauthenticated surface in the API:
gateways cannot log in. Authenticity comes from each gateway's HMAC check in
``parse_webhook`` (constant-time compare); a bad signature is a 400 envelope
and an unknown payment a 404 - both make the gateway retry/alert instead of
silently succeeding. Replays of a paid payment return 200 without
re-crediting (TERMINAL_STATUSES guard in the service).
"""

from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from ninja import Router

from apps.payments import services
from apps.payments.exceptions import WebhookRejectedError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.models import Payment
from apps.payments.schemas import PaymentSummary

router = Router(tags=["payments-webhooks"])


@router.post(
    "/webhooks/{gateway_name}",
    auth=None,
    response=PaymentSummary,
    summary="Gateway webhook (server-to-server)",
    include_in_schema=False,
)
def payment_webhook(request: HttpRequest, gateway_name: str) -> Payment:
    gateway = gateway_by_name(gateway_name)
    if gateway is None:
        raise WebhookRejectedError(str(_("Unknown payment gateway.")))
    try:
        event = gateway.parse_webhook(
            headers=request.headers, params=request.GET, body=request.body
        )
    except WebhookVerificationError as exc:
        raise WebhookRejectedError(str(_("Webhook verification failed."))) from exc
    return services.payment_apply_gateway_event(gateway_name=gateway_name, event=event)
