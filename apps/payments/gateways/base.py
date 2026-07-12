"""Gateway contract + DTOs + money helpers (leaf - importable everywhere).

Every gateway plants ``CheckoutRequest.reference`` (= Payment.idempotency_key)
at the provider and echoes it back in ``WebhookEvent.reference`` - that is how
a webhook finds its Payment row. Signature verification lives in each
gateway's ``parse_webhook`` and REALLY verifies (HMAC over the provider's
documented fields, constant-time compare) - never an echoed shared secret.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from typing import Protocol


class GatewayResponseError(Exception):
    """The gateway answered 2xx but the payload has no usable shape."""


class WebhookVerificationError(Exception):
    """Signature missing or wrong - the request is not from the gateway."""


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


@dataclass(frozen=True, slots=True)
class CheckoutSession:
    charge_id: str
    checkout_url: str
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    reference: str  # the planted idempotency key, echoed back
    transaction_id: str
    is_paid: bool
    status: str  # gateway-native status string (audit/logging)
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChargeStatus:
    transaction_id: str
    is_paid: bool
    status: str
    raw: dict[str, Any]


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


_MINOR_UNIT_EXPONENT = {"SAR": 2, "EGP": 2}


def to_minor_units(amount: Decimal, currency: str) -> int:
    """Decimal major units -> integer minor units (never float math)."""
    exponent = _MINOR_UNIT_EXPONENT[currency]
    quantum = Decimal(1).scaleb(-exponent)  # 0.01 for 2-decimal currencies
    quantized = amount.quantize(quantum)
    if quantized != amount:
        msg = f"{amount} has more than {exponent} decimal places for {currency}"
        raise ValueError(msg)
    return int(quantized.scaleb(exponent))
