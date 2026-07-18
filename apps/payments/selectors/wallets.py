"""Reads for wallets."""

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import WalletNotFoundError
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.users.models import User


def wallet_get(*, user: User) -> Wallet:
    """The user's wallet - provisioned at signup, so absence is a 404."""
    try:
        return Wallet.objects.get(user=user)
    except Wallet.DoesNotExist as exc:
        raise WalletNotFoundError(str(_("Wallet not found."))) from exc


def wallet_transaction_list(*, user: User) -> QuerySet[WalletTransaction]:
    return WalletTransaction.objects.filter(wallet__user=user)
