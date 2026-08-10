from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.payments.constants import WalletTransactionKind


class WalletTransaction(BaseModel):
    wallet = models.ForeignKey(
        "payments.Wallet",
        on_delete=models.PROTECT,
        related_name="transactions",
        verbose_name=_("wallet"),
    )
    kind = models.CharField(_("kind"), max_length=20, choices=WalletTransactionKind)
    amount = models.DecimalField(_("amount"), max_digits=12, decimal_places=2)
    balance_after = models.DecimalField(
        _("balance after"), max_digits=12, decimal_places=2
    )
    payment = models.ForeignKey(
        "payments.Payment",
        on_delete=models.PROTECT,
        related_name="wallet_transactions",
        verbose_name=_("payment"),
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(  # staff member behind a manual adjustment
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("actor"),
        null=True,
        blank=True,
    )
    note = models.CharField(_("note"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("wallet transaction")
        verbose_name_plural = _("wallet transactions")

    def __str__(self) -> str:
        return f"{self.kind} {self.amount} (balance {self.balance_after})"
