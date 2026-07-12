from django.db import models
from django.utils.translation import gettext_lazy as _


class Currency(models.TextChoices):
    SAR = "SAR", _("Saudi riyal")
    EGP = "EGP", _("Egyptian pound")


class PaymentKind(models.TextChoices):
    WALLET_TOPUP = "wallet_topup", _("Wallet top-up")
    OTHER = "other", _("Other")


class PaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    REFUNDED = "refunded", _("Refunded")


#: Statuses a gateway event must never overwrite (idempotent replays).
TERMINAL_STATUSES = frozenset({PaymentStatus.PAID, PaymentStatus.REFUNDED})


class GatewayName(models.TextChoices):
    TAP = "tap", "Tap"
    PAYMOB = "paymob", "Paymob"
    FAKE = "fake", _("Fake (local)")


class WalletTransactionKind(models.TextChoices):
    TOPUP = "topup", _("Top-up")
    REFUND = "refund", _("Refund")
    ADJUSTMENT = "adjustment", _("Manual adjustment")
    PAYMENT = "payment", _("Payment")
