"""Tap (tap.company) - SAR. Charges API v2, Bearer secret key.

Webhook verification: Tap sends a ``hashstring`` header = HMAC-SHA256 over
the documented concatenation
``x_id{id}x_amount{amount}x_currency{currency}x_gateway_reference{...}
x_payment_reference{...}x_status{status}x_created{created}``
keyed with the SAME secret API key (developers.tap.company/docs/webhook).
Amount is formatted with the currency's decimal places.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import phonenumbers
from django.conf import settings

from apps.common.http import request_json
from apps.payments.gateways.base import ChargeStatus
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.base import WebhookVerificationError

_BASE = "https://api.tap.company/v2"
_PAID_STATUS = "CAPTURED"


def _secret() -> str:
    key = settings.TAP_SECRET_KEY
    if key is None:
        msg = "TAP_SECRET_KEY is not set"
        raise GatewayResponseError(msg)
    return str(key.get_secret_value())


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_secret()}"}


class TapGateway:
    name = "tap"

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        customer: dict[str, Any] = {
            "first_name": request.customer_name or "Customer",
            "email": request.customer_email,
        }
        if request.customer_phone:
            parsed = phonenumbers.parse(request.customer_phone)
            customer["phone"] = {
                "country_code": parsed.country_code,
                "number": parsed.national_number,
            }
        response = request_json(
            service="tap",
            method="POST",
            url=f"{_BASE}/charges/",
            headers=_headers(),
            json={
                "amount": str(request.amount),
                "currency": request.currency,
                "customer_initiated": True,
                "threeDSecure": True,
                "save_card": False,
                "description": request.description,
                "reference": {
                    # The planted reference - webhooks echo it back.
                    "transaction": request.reference,
                    "order": request.reference,
                },
                "source": {"id": "src_all"},
                "post": {"url": request.webhook_url},
                "redirect": {"url": request.redirect_url},
                "customer": customer,
            },
            retry="connect-only",  # POST: only provably-unsent requests retry
        )
        payload = response.json()
        charge_id = payload.get("id")
        checkout_url = payload.get("transaction", {}).get("url")
        if not charge_id or not checkout_url:
            msg = f"tap charge response missing id/url: {payload}"
            raise GatewayResponseError(msg)
        return CheckoutSession(
            charge_id=str(charge_id), checkout_url=str(checkout_url), raw=payload
        )

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> WebhookEvent:
        posted = headers.get("hashstring", "")
        if not posted:
            msg = "missing hashstring header"
            raise WebhookVerificationError(msg)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            msg = "webhook body is not JSON"
            raise WebhookVerificationError(msg) from exc
        if not hmac.compare_digest(_expected_hashstring(payload), posted):
            msg = "hashstring mismatch"
            raise WebhookVerificationError(msg)
        status = str(payload.get("status", ""))
        return WebhookEvent(
            reference=str(payload.get("reference", {}).get("transaction", "")),
            transaction_id=str(payload.get("id", "")),
            is_paid=status.upper() == _PAID_STATUS,
            status=status,
            raw=payload,
        )

    def fetch_status(self, *, charge_id: str, reference: str) -> ChargeStatus:
        response = request_json(
            service="tap",
            method="GET",
            url=f"{_BASE}/charges/{charge_id}",
            headers=_headers(),
            retry="transient",  # GET is idempotent
        )
        payload = response.json()
        status = str(payload.get("status", ""))
        return ChargeStatus(
            transaction_id=str(payload.get("id", "")),
            is_paid=status.upper() == _PAID_STATUS,
            status=status,
            raw=payload,
        )

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        response = request_json(
            service="tap",
            method="POST",
            url=f"{_BASE}/refunds/",
            headers=_headers(),
            json={
                "charge_id": transaction_id,
                "amount": str(amount),
                "currency": currency,
                "reason": "requested_by_customer",
            },
            retry="connect-only",
        )
        payload = response.json()
        status = str(payload.get("status", "")).upper()
        return RefundResult(
            ok=status in {"REFUNDED", "ACCEPTED", "PENDING"}, raw=payload
        )


def _expected_hashstring(payload: dict[str, Any]) -> str:
    amount = _format_amount(payload.get("amount", ""), str(payload.get("currency", "")))
    reference = payload.get("reference", {})
    transaction = payload.get("transaction", {})
    concatenated = (
        f"x_id{payload.get('id', '')}"
        f"x_amount{amount}"
        f"x_currency{payload.get('currency', '')}"
        f"x_gateway_reference{reference.get('gateway', '')}"
        f"x_payment_reference{reference.get('payment', '')}"
        f"x_status{payload.get('status', '')}"
        f"x_created{transaction.get('created', '')}"
    )
    return hmac.new(
        _secret().encode(), concatenated.encode(), hashlib.sha256
    ).hexdigest()


def _format_amount(amount: object, currency: str) -> str:
    """Tap hashes the amount formatted to the currency's decimal places."""
    decimals = 2  # SAR (and every currency this scaffold ships) uses 2
    try:
        return f"{Decimal(str(amount)):.{decimals}f}"
    except ArithmeticError:
        return str(amount)
