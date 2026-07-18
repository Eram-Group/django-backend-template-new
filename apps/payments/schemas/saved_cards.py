import uuid
from datetime import datetime

from ninja import Schema

from apps.payments.constants import GatewayName


class SavedCardSummary(Schema):
    """Display data only - the gateway token/customer/agreement references
    never leave the server."""

    id: uuid.UUID
    gateway: GatewayName
    brand: str
    last4: str
    exp_month: int | None
    exp_year: int | None
    created_at: datetime
