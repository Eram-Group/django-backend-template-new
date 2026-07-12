from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.payments.constants import Currency


class Wallet(BaseModel):
    """One wallet per user, created lazily on first credit.

    ALL balance movement goes through services.wallet_apply (row lock +
    ledger entry) - nothing else may write ``balance``.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="wallet",
        verbose_name=_("user"),
    )
    balance = models.DecimalField(
        _("balance"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency = models.CharField(_("currency"), max_length=3, choices=Currency)

    class Meta:
        verbose_name = _("wallet")
        verbose_name_plural = _("wallets")

    def __str__(self) -> str:
        return f"{self.user} wallet ({self.balance} {self.currency})"
