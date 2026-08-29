from datetime import timedelta

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.users.constants import Language


class Currency(models.TextChoices):
    SAR = "SAR", _("Saudi riyal")
    EGP = "EGP", _("Egyptian pound")


#: Currency of the wallet provisioned at signup, by the user's language -
#: the only market signal a fresh account carries. Every Language member is
#: mapped (test_constants pins that); both currently land on SAR, the
#: launch market. Sending a language to EGP is a one-line change here,
#: never an inline condition at the call site.
CURRENCY_BY_LANGUAGE: dict[Language, Currency] = {
    Language.ARABIC: Currency.SAR,
    Language.ENGLISH: Currency.SAR,
}


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

#: How long a PENDING checkout may stay open before the reconcile sweep
#: marks it FAILED (abandoned). Must exceed every gateway's hosted-session
#: lifetime (Paymob intentions live at most one hour) so a checkout that is
#: still payable is never expired underneath the customer. FAILED stays
#: non-terminal, so a late webhook still heals a wrongly-expired row.
PENDING_EXPIRY = timedelta(hours=2)


class GatewayName(models.TextChoices):
    TAP = "tap", "Tap"
    PAYMOB = "paymob", "Paymob"


class WalletTransactionKind(models.TextChoices):
    TOPUP = "topup", _("Top-up")
    REFUND = "refund", _("Refund")
    ADJUSTMENT = "adjustment", _("Manual adjustment")
    PAYMENT = "payment", _("Payment")
