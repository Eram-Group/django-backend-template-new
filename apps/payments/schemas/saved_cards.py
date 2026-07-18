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


class CardAddIn(Schema):
    """Optional body for POST /cards/add.

    ``card_token`` is a one-time token minted by the gateway's own card
    component embedded in the frontend (Tap Card SDK ``tok_...``) - never a
    raw PAN. Empty = the hosted page collects the card instead.
    """

    card_token: str = ""
