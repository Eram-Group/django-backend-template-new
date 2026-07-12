"""Wallet writes. wallet_apply is the ONLY code that moves a balance.

The old template's race (``wallet.balance += amount; wallet.save()`` with no
lock) is fixed here: the Wallet row is locked with select_for_update for the
whole read-modify-write, which also makes ``balance_after`` and the
non-negative check trustworthy. F() expressions alone could not provide
either.
"""

import uuid
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.payments.constants import Currency
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import InsufficientBalanceError
from apps.payments.models import Payment
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.users.models import User


def wallet_get_or_create(*, user: User, currency: Currency | str) -> Wallet:
    wallet, _created = Wallet.objects.get_or_create(
        user=user, defaults={"currency": currency}
    )
    return wallet


def wallet_apply(
    *,
    wallet_id: uuid.UUID,
    amount: Decimal,
    kind: WalletTransactionKind,
    payment: Payment | None = None,
    actor: User | None = None,
    note: str = "",
) -> WalletTransaction:
    """Move the balance by signed ``amount`` and append the ledger row."""
    with transaction.atomic():
        wallet = Wallet.objects.select_for_update().get(pk=wallet_id)  # row lock
        wallet.balance += amount
        if wallet.balance < 0:
            raise InsufficientBalanceError(str(_("Insufficient wallet balance.")))
        wallet.full_clean()
        wallet.save(update_fields=["balance", "updated_at"])
        entry = WalletTransaction(
            wallet=wallet,
            kind=kind,
            amount=amount,
            balance_after=wallet.balance,
            payment=payment,
            actor=actor,
            note=note,
        )
        entry.full_clean()
        entry.save()
        return entry
