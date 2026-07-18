import uuid
from datetime import datetime
from decimal import Decimal

from ninja import Field
from ninja import Schema

from apps.payments.constants import Currency
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus


class PaymentSummary(Schema):
    id: uuid.UUID
    kind: PaymentKind
    amount: Decimal
    currency: Currency
    status: PaymentStatus
    created_at: datetime


class PaymentDetail(PaymentSummary):
    gateway: GatewayName
    description: str
    checkout_url: str
    paid_at: datetime | None
    saved_card_id: uuid.UUID | None


class PaymentCreateIn(Schema):
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Currency
    kind: PaymentKind = PaymentKind.OTHER
    description: str = ""
    #: Vault the card entered at checkout for one-click reuse (ignored when
    #: saved_card_id is sent - a stored card cannot be re-saved).
    save_card: bool = False
    #: Pay one-click with this stored card instead of entering card details.
    saved_card_id: uuid.UUID | None = None
