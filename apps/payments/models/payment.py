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
    """One checkout attempt against a payment gateway.

    Status transitions happen ONLY in services (webhook/verify/refund);
    money is plain Decimal + a currency column - integer minor units exist
    only on the wire (gateways.base.to_minor_units).
    """

    # PROTECT: financial history outlives everything; account "deletion" is
    # deactivation (users.user_deactivate), never a row delete.
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
    # The reference WE plant at the gateway (Tap reference.transaction /
    # Paymob special_reference) - stable across create retries, and how
    # webhooks find their Payment row.
    idempotency_key = models.UUIDField(
        _("idempotency key"), default=uuid.uuid4, unique=True, editable=False
    )
    # Checkout-time id (Tap charge id / Paymob intention id) ...
    gateway_charge_id = models.CharField(
        _("gateway charge id"), max_length=255, blank=True
    )
    # ... vs the settled transaction id reported by the webhook (refunds
    # target this one on Paymob).
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
    # Write-ahead marker for the refund executor: stamped (and committed)
    # BEFORE the provider refund call, which is NOT idempotent at Tap/Paymob.
    # A REFUND_PENDING row with this set must never re-hit the gateway -
    # it needs manual reconciliation against the provider dashboard.
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
            # DB backstop for the MinValueValidator - guards any write path
            # that bypasses full_clean (raw SQL, bulk updates).
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="payment_amount_positive",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency} {self.status} ({self.user})"
