from django.db import models
from django.utils.translation import gettext_lazy as _


class Currency(models.TextChoices):
    SAR = "SAR", _("Saudi riyal")
    EGP = "EGP", _("Egyptian pound")


#: Currency of the wallet provisioned at signup (user_post_signup).
DEFAULT_CURRENCY = Currency.SAR


class PaymentKind(models.TextChoices):
    WALLET_TOPUP = "wallet_topup", _("Wallet top-up")
    OTHER = "other", _("Other")


class PaymentStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Failed")
    #: Refund interlock: the gateway refund call is in flight. Blocks a
    #: second concurrent refund and shields the row from webhook replays.
    REFUND_PENDING = "refunding", _("Refund pending")
    REFUNDED = "refunded", _("Refunded")


#: Statuses a gateway event must never overwrite (idempotent replays).
#: REFUND_PENDING is included so a replayed "paid" webhook cannot flip the
#: row back to PAID (and re-credit the wallet) while a refund is in flight.
TERMINAL_STATUSES = frozenset(
    {PaymentStatus.PAID, PaymentStatus.REFUND_PENDING, PaymentStatus.REFUNDED}
)


class GatewayName(models.TextChoices):
    TAP = "tap", "Tap"
    PAYMOB = "paymob", "Paymob"
    FAKE = "fake", _("Fake (local)")


class WalletTransactionKind(models.TextChoices):
    TOPUP = "topup", _("Top-up")
    REFUND = "refund", _("Refund")
    ADJUSTMENT = "adjustment", _("Manual adjustment")
    PAYMENT = "payment", _("Payment")
