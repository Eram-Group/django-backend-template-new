"""Reads for wallets."""

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import WalletNotFoundError
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.users.models import User


def get_user_wallet(*, user: User) -> Wallet:
    try:
        return Wallet.objects.get(user=user)
    except Wallet.DoesNotExist as exc:
        raise WalletNotFoundError(str(_("Wallet not found."))) from exc


def get_user_wallet_transactions(*, user: User) -> QuerySet[WalletTransaction]:
    return WalletTransaction.objects.filter(wallet__user=user)
