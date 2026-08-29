"""Gateway contract + DTOs + money helpers (leaf - importable everywhere).

Every gateway plants ``CheckoutRequest.reference`` (= Payment.idempotency_key)
at the provider and echoes it back in ``PaymentEvent.reference`` - that is how
a gateway-reported outcome finds its Payment row. One outcome shape serves
every channel (webhook, synchronous charge response, status inquiry) so the
service applies them all through the same idempotent transition, and every
outcome carries the provider's amount/currency so the service can prove the
event is about THIS payment at THIS price before any money moves.

Signature verification lives in each gateway's ``parse_webhook`` and REALLY
verifies (HMAC over the provider's documented fields, constant-time compare) -
never an echoed shared secret. Card-token callbacks (Paymob TOKEN) carry no
payment reference at all, so they are their own event type rather than a
payment event with blank fields.

Gateway constructors read their settings once and refuse to build when a
required key is missing (``GatewayConfigurationError``) - the registry in
``gateways/__init__`` turns that into the API's 503.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from typing import Protocol
from typing import runtime_checkable


class GatewayError(Exception):
    """Base for gateway-side failures the service maps to a 503."""


class GatewayResponseError(GatewayError):
    """The gateway answered 2xx but the payload has no usable shape."""


class GatewayConfigurationError(GatewayError):
    """A required gateway setting is missing - raised by the constructor."""


class WebhookVerificationError(Exception):
    """Signature missing or wrong - the request is not from the gateway."""


@dataclass(frozen=True, slots=True)
class SavedCardRef:
    """What a gateway needs to charge or delete a stored card (input side)."""

    token: str  # Tap card_id ("card_...") / Paymob card token
    customer_id: str  # Tap "cus_..."; "" for Paymob (it has no customer object)
    agreement_id: str  # Tap "payment_agreement_..."; "" for Paymob


@dataclass(frozen=True, slots=True)
class SavedCardData:
    """Card payload parsed out of a charge response/webhook (output side).

    The provider's card fingerprint is NOT here on purpose: only Tap has one
    and it is read from its Card API (``CardVaultGateway``), never trusted
    from an undocumented field on the charge payload.
    """

    token: str
    customer_id: str  # "" for Paymob
    agreement_id: str  # "" for Paymob, and for Tap accounts without agreements
    brand: str
    last4: str
    #: None when the provider did not send an expiry (Paymob token callbacks
    #: before Aug-2026 carry none; Tap's charge card object may omit it).
    exp_month: int | None
    exp_year: int | None
    email: str  # billing email echoed by the gateway; "" when the shape has none


@dataclass(frozen=True, slots=True)
class CheckoutRequest:
    reference: str  # Payment.idempotency_key - stable across create retries
    amount: Decimal
    currency: str
    description: str
    customer_email: str
    customer_name: str  # full name - the service refuses a checkout without one
    customer_phone: str  # E164 - the service refuses a checkout without one
    webhook_url: str
    redirect_url: str
    #: Pay WITH this stored card (CIT via create_checkout, MIT via
    #: charge_saved). None = a new card is entered at checkout, and every
    #: new-card checkout requests vaulting (saving is not client-optional).
    saved_card: SavedCardRef | None
    #: The gateway customer the user's other cards already live under (Tap
    #: "cus_..."); "" = let the gateway create one. Reusing it is what makes
    #: the same physical card come back with the same card id.
    customer_id: str


@dataclass(frozen=True, slots=True)
class PaymentEvent:
    """One gateway-reported outcome for a Payment - webhook, synchronous
    charge response or status inquiry alike."""

    reference: str  # the planted idempotency key, echoed back
    #: The settled transaction id. "" ONLY on a refund/void/capture child
    #: action (Paymob), which must not replace the id our refund targets.
    transaction_id: str
    is_paid: bool
    #: Informational (still in flight, or a refund/void/capture child): the
    #: service records the callback and never transitions the row.
    is_pending: bool
    status: str  # gateway-native status string (audit/logging)
    #: What the gateway says was charged, in minor units, and in what
    #: currency - the service cross-checks both against the row before a
    #: transition. On a child action they describe the child (a partial
    #: refund's amount), which is why pending events skip that check.
    amount_minor: int
    currency: str
    saved_card: SavedCardData | None  # the card vaulted by this charge, if any
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CardTokenEvent:
    """A standalone card-token callback (Paymob TOKEN) - no payment state
    change; the card links to its user by billing email."""

    saved_card: SavedCardData
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    charge_id: str
    checkout_url: str  # "" when there is nothing to redirect to
    raw: dict[str, Any]
    #: A synchronous FINAL outcome (one-click captured/declined). None = the
    #: redirect/webhook/reconcile sweep settles it later.
    outcome: PaymentEvent | None


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
    ) -> PaymentEvent | CardTokenEvent: ...

    def fetch_status(self, *, charge_id: str, reference: str) -> PaymentEvent | None:
        """The provider's current outcome; None when it holds no transaction
        for the reference yet (an unpaid Paymob order) - still pending."""
        ...

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult: ...

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession: ...


@runtime_checkable
class CardVaultGateway(Protocol):
    """A gateway whose vault we can read and prune (Tap's Card API).

    Paymob exposes neither call: its tokens dedupe on their own and cannot be
    detached, so the service checks this capability instead of asking every
    gateway and trusting a made-up answer.
    """

    def saved_card_fingerprint(self, *, saved_card: SavedCardRef) -> str:
        """The provider's hash of the card number - the same physical card
        keeps it across tokens and customers."""
        ...

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> None:
        """Detach the card at the provider; raises when it did not happen."""
        ...


#: Decimal places of every currency this scaffold ships (SAR, EGP). Adding a
#: 3-decimal currency (KWD, BHD) turns this into a per-currency table.
MINOR_UNITS = 2


def to_minor_units(*, amount: Decimal) -> int:
    """Decimal major units -> integer minor units (never float math)."""
    quantum = Decimal(1).scaleb(-MINOR_UNITS)  # 0.01
    quantized = amount.quantize(quantum)
    if quantized != amount:
        msg = f"{amount} has more than {MINOR_UNITS} decimal places"
        raise ValueError(msg)
    return int(quantized.scaleb(MINOR_UNITS))
