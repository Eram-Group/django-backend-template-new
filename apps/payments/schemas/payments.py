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
    client_request_id: uuid.UUID | None
    gateway: GatewayName
    description: str
    checkout_url: str
    paid_at: datetime | None
    saved_card_id: uuid.UUID | None


class PaymentCreateIn(Schema):
    #: The client's key for THIS operation (a fresh uuid per user intent).
    #: A retried POST with the same key returns the payment it already
    #: opened; the same key with a different payload is a 409.
    request_id: uuid.UUID
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    currency: Currency
    kind: PaymentKind  # what the money is for - the client always says
    description: str = ""
    #: Pay one-click with this stored card instead of entering card details.
    #: Absent = a new card is entered at checkout and is always vaulted.
    saved_card_id: uuid.UUID | None = None
