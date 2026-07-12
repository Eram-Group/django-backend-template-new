"""Reads for wallets."""

from django.db.models import QuerySet

from apps.payments.models import WalletTransaction
from apps.users.models import User


def wallet_transaction_list(*, user: User) -> QuerySet[WalletTransaction]:
    return WalletTransaction.objects.filter(wallet__user=user)
