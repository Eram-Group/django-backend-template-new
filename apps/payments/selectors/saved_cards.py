"""Reads for saved cards - always scoped to the owner."""

import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import SavedCardNotFoundError
from apps.payments.models import SavedCard
from apps.users.models import User


def saved_card_list(*, user: User) -> QuerySet[SavedCard]:
    return SavedCard.objects.filter(user=user)


def saved_card_get(*, user: User, pk: uuid.UUID) -> SavedCard:
    try:
        return SavedCard.objects.get(user=user, pk=pk)
    except SavedCard.DoesNotExist as exc:
        raise SavedCardNotFoundError(str(_("Saved card not found."))) from exc
