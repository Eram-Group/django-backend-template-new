"""Reads for payments - always scoped to the owner."""

import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.models import Payment
from apps.users.models import User


def list_user_payments(*, user: User) -> QuerySet[Payment]:
    return Payment.objects.filter(user=user)


def get_user_payment(*, user: User, pk: uuid.UUID) -> Payment:
    try:
        return Payment.objects.get(user=user, pk=pk)
    except Payment.DoesNotExist as exc:
        raise PaymentNotFoundError(str(_("Payment not found."))) from exc
