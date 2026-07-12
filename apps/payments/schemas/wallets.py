import uuid
from datetime import datetime
from decimal import Decimal

from ninja import Schema

from apps.payments.constants import Currency
from apps.payments.constants import WalletTransactionKind


class WalletDetail(Schema):
    balance: Decimal
    currency: Currency


class WalletTransactionSummary(Schema):
    id: uuid.UUID
    kind: WalletTransactionKind
    amount: Decimal
    balance_after: Decimal
    note: str
    created_at: datetime
