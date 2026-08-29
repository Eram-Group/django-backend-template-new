"""Wallet writes: creation at signup, and wallet_apply - the ONLY code
that moves a balance.

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

from apps.payments.constants import CURRENCY_BY_LANGUAGE
from apps.payments.constants import Currency
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import InsufficientBalanceError
from apps.payments.models import Payment
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.users.constants import Language
from apps.users.models import User


def wallet_currency_for(*, language: str) -> Currency:
    """The currency of the wallet a user in ``language`` gets at signup -
    the one place that decision lives (constants.CURRENCY_BY_LANGUAGE). An
    unknown language code raises ``ValueError``."""
    return CURRENCY_BY_LANGUAGE[Language(language)]


def wallet_create(*, user: User, currency: Currency) -> Wallet:
    """Provision the user's wallet - called once, from user_create,
    with ``currency=wallet_currency_for(language=user.language)``.

    Payment flows never create wallets; they resolve the existing one via
    selectors.wallet_get and reject currency mismatches.
    """
    wallet = Wallet(user=user, currency=currency)
    wallet.full_clean()
    wallet.save()
    return wallet


def wallet_apply(
    *,
    wallet_id: uuid.UUID,
    amount: Decimal,
    kind: WalletTransactionKind,
    payment: Payment | None,
    actor: User | None,
    note: str,
) -> WalletTransaction:
    """Move the balance by signed ``amount`` and append the ledger row.

    ``payment`` is the Payment behind a top-up/refund (None for a spend or a
    manual adjustment); ``actor`` the staff member behind a manual movement
    (None when a gateway event or the sweep drove it); ``note`` the human
    reason ("" when the kind and payment say it all).
    """
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
