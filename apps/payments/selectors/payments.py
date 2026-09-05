"""Reads for payments - always scoped to the owner, plus the operator's
"needs attention" view."""

import uuid

from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.payments.constants import PENDING_ATTENTION_AFTER
from apps.payments.constants import PaymentStatus
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.models import Payment
from apps.users.models import User


def payment_list(*, user: User) -> QuerySet[Payment]:
    return Payment.objects.filter(user=user)


def payments_needing_attention() -> QuerySet[Payment]:
    """Rows nothing will move on its own (nothing runs on a timer):

    - PENDING past every gateway's hosted-session lifetime - the customer
      can no longer complete it, so it either lost its webhook (verify with
      the provider) or was abandoned;
    - REFUND_PENDING already sent to the provider - either accepted-not-
      settled (``gateway_refund_id`` set: confirm in the dashboard) or the
      outcome unknown (no id: reconcile by hand).
    """
    stale = timezone.now() - PENDING_ATTENTION_AFTER
    return Payment.objects.filter(
        Q(status=PaymentStatus.PENDING, created_at__lt=stale)
        | Q(status=PaymentStatus.REFUND_PENDING, refund_attempted_at__isnull=False)
    )


def payment_get(*, user: User, pk: uuid.UUID) -> Payment:
    try:
        return Payment.objects.get(user=user, pk=pk)
    except Payment.DoesNotExist as exc:
        raise PaymentNotFoundError(str(_("Payment not found."))) from exc
