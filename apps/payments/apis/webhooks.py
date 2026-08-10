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
