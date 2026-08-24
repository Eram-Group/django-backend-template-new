"""Reads for saved cards - always scoped to the owner."""

import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import SavedCardNotFoundError
from apps.payments.models import SavedCard
from apps.users.models import User


def saved_card_list(*, user: User) -> QuerySet[SavedCard]:
    return SavedCard.objects.filter(user=user)


def saved_card_gateway_customer_id(*, user: User, gateway: str) -> str:
    """The gateway customer the user's cards already live under, or "".

    Sent on new-card checkouts so every card lands under one Tap customer -
    which is what makes the same physical card come back with the same card
    id instead of a new one (Tap: "use a consistent Customer ID").
    """
    return (
        SavedCard.objects.filter(user=user, gateway=gateway)
        .exclude(gateway_customer_id="")
        .order_by("-created_at")
        .values_list("gateway_customer_id", flat=True)
        .first()
        or ""
    )


def saved_card_get(*, user: User, pk: uuid.UUID) -> SavedCard:
    try:
        return SavedCard.objects.get(user=user, pk=pk)
    except SavedCard.DoesNotExist as exc:
        raise SavedCardNotFoundError(str(_("Saved card not found."))) from exc
