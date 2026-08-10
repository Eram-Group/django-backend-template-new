import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.payments.constants import Currency
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus


class Payment(BaseModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("user"),
    )
    kind = models.CharField(_("kind"), max_length=20, choices=PaymentKind)
    description = models.CharField(_("description"), max_length=255, blank=True)
    amount = models.DecimalField(
        _("amount"),
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    currency = models.CharField(_("currency"), max_length=3, choices=Currency)
    status = models.CharField(
        _("status"),
        max_length=10,
        choices=PaymentStatus,
        default=PaymentStatus.PENDING,
    )
    gateway = models.CharField(_("gateway"), max_length=10, choices=GatewayName)
    saved_card = models.ForeignKey(
        "payments.SavedCard",
        on_delete=models.SET_NULL,
        related_name="payments",
        verbose_name=_("saved card"),
        null=True,
        blank=True,
    )
    save_card_requested = models.BooleanField(_("save card requested"), default=False)
    idempotency_key = models.UUIDField(
        _("idempotency key"), default=uuid.uuid4, unique=True, editable=False
    )
    gateway_charge_id = models.CharField(
        _("gateway charge id"), max_length=255, blank=True
    )
    gateway_transaction_id = models.CharField(
        _("gateway transaction id"), max_length=255, blank=True
    )
    checkout_url = models.URLField(_("checkout URL"), max_length=500, blank=True)
    gateway_response = models.JSONField(  # raw create-charge response (audit)
        _("gateway response"), null=True, blank=True
    )
    gateway_callback = models.JSONField(  # raw last webhook payload (audit)
        _("gateway callback"), null=True, blank=True
    )
    paid_at = models.DateTimeField(_("paid at"), null=True, blank=True)
    refund_attempted_at = models.DateTimeField(
        _("refund attempted at"), null=True, blank=True, editable=False
    )

    class Meta:
        verbose_name = _("payment")
        verbose_name_plural = _("payments")
        indexes = [
            models.Index(fields=["user", "status"]),
            models.Index(fields=["gateway_charge_id"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency} {self.status} ({self.user})"
