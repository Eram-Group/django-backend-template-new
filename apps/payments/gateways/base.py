"""Gateway contract + DTOs + money helpers (leaf - importable everywhere).

Every gateway plants ``CheckoutRequest.reference`` (= Payment.idempotency_key)
at the provider and echoes it back in ``WebhookEvent.reference`` - that is how
a webhook finds its Payment row. The one exception is ``CARD_TOKEN`` events
(Paymob sends the card token as a standalone callback carrying no reference) -
those never touch a Payment row and link to the user by billing email instead.
Signature verification lives in each gateway's ``parse_webhook`` and REALLY
verifies (HMAC over the provider's documented fields, constant-time compare) -
never an echoed shared secret.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from typing import Protocol


class GatewayResponseError(Exception):
    """The gateway answered 2xx but the payload has no usable shape."""


class WebhookVerificationError(Exception):
    """Signature missing or wrong - the request is not from the gateway."""


class WebhookEventKind(StrEnum):
    PAYMENT = "payment"
    #: Standalone card-token callback (Paymob TOKEN) - no payment state
    #: change; ``reference``/``transaction_id`` are empty on these events.
    CARD_TOKEN = "card_token"  # noqa: S105 - event kind, not a secret


@dataclass(frozen=True, slots=True)
class SavedCardRef:
    """What a gateway needs to charge or delete a stored card (input side)."""

    token: str  # Tap card_id ("card_...") / Paymob card token
    customer_id: str  # Tap "cus_..."; "" for Paymob
    agreement_id: str  # Tap "payment_agreement_..."; "" for Paymob


@dataclass(frozen=True, slots=True)
class SavedCardData:
    """Card payload parsed out of a charge response/webhook (output side)."""

    token: str
    customer_id: str
    agreement_id: str
    brand: str
    last4: str
    exp_month: int | None  # Paymob provides neither expiry field
    exp_year: int | None
    email: str  # billing email echoed by the gateway ("" when absent)


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    reference: str  # Payment.idempotency_key - stable across create retries
    amount: Decimal
    currency: str
    description: str
    customer_email: str
    customer_name: str
    customer_phone: str  # E164 or ""
    webhook_url: str
    redirect_url: str
    #: Pay WITH this stored card (CIT via create_checkout, MIT via
    #: charge_saved). None = a new card is entered at checkout, and every
    #: new-card checkout requests vaulting (saving is not client-optional).
    saved_card: SavedCardRef | None = None


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    charge_id: str
    checkout_url: str  # "" = no redirect; the outcome was synchronous
    raw: dict[str, Any]
    is_paid: bool = False  # meaningful only when status != ""
    #: Gateway-native FINAL status; "" = pending (redirect/webhook settles it).
    status: str = ""
    transaction_id: str = ""  # settled txn id for synchronous outcomes


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    reference: str  # the planted idempotency key, echoed back
    transaction_id: str  # "" = do not touch Payment.gateway_transaction_id
    is_paid: bool
    status: str  # gateway-native status string (audit/logging)
    raw: dict[str, Any]
    kind: WebhookEventKind = WebhookEventKind.PAYMENT
    saved_card: SavedCardData | None = None
    #: The event is informational (still in flight, or a refund/void/capture
    #: child transaction): record the callback, never transition the row.
    is_pending: bool = False
    #: What the gateway says was charged, in minor units - set when the
    #: provider signs these fields so the service can cross-check the row.
    amount_minor: int | None = None
    currency: str = ""


@dataclass(frozen=True, slots=True)
class ChargeStatus:
    transaction_id: str
    is_paid: bool
    status: str
    raw: dict[str, Any]
    #: Lets payment_verify persist a card when the webhook was lost.
    saved_card: SavedCardData | None = None
    is_pending: bool = False
    amount_minor: int | None = None
    currency: str = ""


@dataclass(frozen=True, slots=True)
class RefundResult:
    ok: bool
    raw: dict[str, Any]


class PaymentGateway(Protocol):
    name: str

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession: ...

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> WebhookEvent: ...

    def fetch_status(self, *, charge_id: str, reference: str) -> ChargeStatus: ...

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult: ...

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession: ...

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> bool: ...


_MINOR_UNIT_EXPONENT = {"SAR": 2, "EGP": 2}


def to_minor_units(*, amount: Decimal, currency: str) -> int:
    """Decimal major units -> integer minor units (never float math)."""
    exponent = _MINOR_UNIT_EXPONENT[currency]
    quantum = Decimal(1).scaleb(-exponent)  # 0.01 for 2-decimal currencies
    quantized = amount.quantize(quantum)
    if quantized != amount:
        msg = f"{amount} has more than {exponent} decimal places for {currency}"
        raise ValueError(msg)
    return int(quantized.scaleb(exponent))
