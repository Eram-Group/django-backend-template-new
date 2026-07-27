"""Saved-card writes: webhook-driven store, user-driven delete.

Cards are vaulted at the gateway; these services only manage our reference
rows. Storing is idempotent on (gateway, token) so webhook replays and the
verify fallback can never duplicate a card.
"""

import structlog
from django.utils.translation import gettext_lazy as _

from apps.common.http import OutboundError
from apps.payments.exceptions import SavedCardNotFoundError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import SavedCardData
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEvent
from apps.payments.models import SavedCard
from apps.users.models import User

logger = structlog.get_logger(__name__)


def saved_card_store(*, user: User, gateway: str, data: SavedCardData) -> SavedCard:
    """Idempotent upsert on (gateway, token).

    A token resurfacing under a new account is REASSIGNED: completing 3DS on
    the hosted page proves possession, and one provider token maps to exactly
    one row.
    """
    card, _created = SavedCard.objects.update_or_create(
        gateway=gateway,
        token=data.token,
        defaults={
            "user": user,
            "gateway_customer_id": data.customer_id,
            "gateway_agreement_id": data.agreement_id,
            "brand": data.brand,
            "last4": data.last4,
            "exp_month": data.exp_month,
            "exp_year": data.exp_year,
        },
    )
    # update_or_create saves before validation can run; full_clean after
    # still protects - the caller's transaction discards the write when
    # this raises.
    card.full_clean()
    return card


def saved_card_store_from_event(
    *, gateway_name: str, event: WebhookEvent
) -> SavedCard | None:
    """Standalone card-token callbacks (Paymob TOKEN): link by billing email.

    We always send ``user.email`` as the billing email and Paymob echoes it
    verbatim; emails are unique. An unmatched email is logged and dropped -
    a gateway retry can never fix it, so the webhook still acks 200 and the
    customer simply saves the card again on their next checkout.
    """
    data = event.saved_card
    if data is None or not data.token:
        return None
    user = User.objects.filter(email__iexact=data.email).first()
    if user is None:
        logger.warning(
            "saved_card_user_not_found", gateway=gateway_name, email=data.email
        )
        return None
    return saved_card_store(user=user, gateway=gateway_name, data=data)


def saved_card_delete(*, user: User, saved_card: SavedCard) -> None:
    """Delete the row; best-effort detach at the gateway first (Tap only).

    A gateway failure is non-fatal on purpose: the local row is what makes
    the token chargeable BY US, so the user's intent wins immediately; a
    dangling provider-side card is inert and logged for follow-up.
    """
    if saved_card.user_id != user.pk:
        raise SavedCardNotFoundError(str(_("Saved card not found.")))
    gateway = gateway_by_name(saved_card.gateway)
    if gateway is not None:
        try:
            gateway.delete_saved_card(
                saved_card=SavedCardRef(
                    token=saved_card.token,
                    customer_id=saved_card.gateway_customer_id,
                    agreement_id=saved_card.gateway_agreement_id,
                )
            )
        except OutboundError, GatewayResponseError:
            logger.warning(
                "saved_card_gateway_delete_failed",
                saved_card_id=str(saved_card.pk),
                gateway=saved_card.gateway,
            )
    saved_card.delete()
