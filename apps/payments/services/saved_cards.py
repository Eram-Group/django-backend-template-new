"""Saved-card writes: webhook-driven store, user-driven delete.

Cards are vaulted at the gateway; these services only manage our reference
rows. Storing is idempotent on (gateway, token) - so webhook replays and the
verify fallback can never duplicate a row - and on the provider's card
fingerprint, so the same physical card re-vaulted under a new token is one
row too.
"""

import structlog
from django.db import transaction
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
    """Idempotent upsert - one row per provider token AND per physical card.

    Two things can hand us a card we already hold:

    * the same token again (webhook replay, verify fallback) - matched on
      (gateway, token). A token resurfacing under a new account is
      REASSIGNED, mirroring device_register: completing 3DS on the hosted
      page proves possession, and one provider token maps to one row.
    * the same physical card vaulted again under another provider customer
      (Tap mints a new card id per customer) - matched on the provider's
      ``fingerprint``. The existing row is repointed at the newest ids (the
      ones that just passed 3DS) and the superseded card is detached at the
      gateway once this commits, so the provider vault does not fill with
      copies either.
    """
    fingerprint = data.fingerprint or _fetch_fingerprint(gateway=gateway, data=data)
    fields = {
        "user": user,
        "token": data.token,
        "gateway_customer_id": data.customer_id,
        "gateway_agreement_id": data.agreement_id,
        "fingerprint": fingerprint,
        "brand": data.brand,
        "last4": data.last4,
        "exp_month": data.exp_month,
        "exp_year": data.exp_year,
    }
    superseded: SavedCardRef | None = None
    with transaction.atomic():
        card = (
            SavedCard.objects.select_for_update()
            .filter(gateway=gateway, token=data.token)
            .first()
        )
        if card is None and fingerprint:
            card = (
                SavedCard.objects.select_for_update()
                .filter(user=user, gateway=gateway, fingerprint=fingerprint)
                .first()
            )
        if card is None:
            card = SavedCard(gateway=gateway)
        elif card.token != data.token:
            superseded = SavedCardRef(
                token=card.token,
                customer_id=card.gateway_customer_id,
                agreement_id=card.gateway_agreement_id,
            )
        for name, value in fields.items():
            setattr(card, name, value)
        card.full_clean()
        card.save()
    if superseded is not None:
        logger.info(
            "saved_card_superseded",
            saved_card_id=str(card.pk),
            gateway=gateway,
            old_token=superseded.token,
        )
        transaction.on_commit(
            lambda: _detach_at_gateway(gateway_name=gateway, saved_card=superseded)
        )
    return card


def _fetch_fingerprint(*, gateway: str, data: SavedCardData) -> str:
    """The webhook/charge payload does not always carry the fingerprint; the
    provider's card API does. "" on failure - the row is still stored, just
    without fingerprint dedup for this card."""
    provider = gateway_by_name(gateway)
    if provider is None:
        return ""
    return provider.saved_card_fingerprint(
        saved_card=SavedCardRef(
            token=data.token,
            customer_id=data.customer_id,
            agreement_id=data.agreement_id,
        )
    )


def _detach_at_gateway(*, gateway_name: str, saved_card: SavedCardRef) -> None:
    """Best-effort delete of a card we no longer reference. A dangling
    provider-side card is inert; it is logged for follow-up."""
    provider = gateway_by_name(gateway_name)
    if provider is None:
        return
    try:
        provider.delete_saved_card(saved_card=saved_card)
    except OutboundError, GatewayResponseError:
        logger.warning(
            "saved_card_superseded_delete_failed",
            gateway=gateway_name,
            token=saved_card.token,
        )


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
            "saved_card_user_not_found",
            gateway=gateway_name,
            email=_mask_email(data.email),
        )
        return None
    return saved_card_store(user=user, gateway=gateway_name, data=data)


def _mask_email(email: str) -> str:
    """``omar@example.com`` -> ``o***@example.com`` (logs carry no PII)."""
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    return f"{local[:1]}***@{domain}"


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
