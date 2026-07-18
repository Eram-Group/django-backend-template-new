"""Paymob (paymob.com) - EGP. Intention API + unified checkout.

Webhook verification: the transaction-processed callback carries an ``hmac``
query parameter = HMAC-SHA512 (hex, lowercase) over the concatenated values
of 20 documented transaction fields in lexicographical key order, keyed with
the dashboard HMAC secret (developers.paymob.com "HMAC Calculation").
Booleans serialize as ``true``/``false``.

``special_reference`` (= our idempotency key) makes charge creation
idempotent at Paymob AND comes back as ``order.merchant_order_id`` in the
callback - the old template generated a fresh uuid per attempt, defeating
both.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from django.conf import settings

from apps.common.http import request_json
from apps.payments.gateways.base import ChargeStatus
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.base import to_minor_units

_BASE = "https://accept.paymob.com"

# The documented field list, ALREADY in lexicographical order; dots reach
# into nested objects.
_HMAC_FIELDS = (
    "amount_cents",
    "created_at",
    "currency",
    "error_occured",
    "has_parent_transaction",
    "id",
    "integration_id",
    "is_3d_secure",
    "is_auth",
    "is_capture",
    "is_refunded",
    "is_standalone_payment",
    "is_voided",
    "order.id",
    "owner",
    "pending",
    "source_data.pan",
    "source_data.sub_type",
    "source_data.type",
    "success",
)


def _secret() -> str:
    key = settings.PAYMOB_SECRET_KEY
    if key is None:
        msg = "PAYMOB_SECRET_KEY is not set"
        raise GatewayResponseError(msg)
    return str(key.get_secret_value())


def _headers() -> dict[str, str]:
    return {"Authorization": f"Token {_secret()}"}


class PaymobGateway:
    name = "paymob"

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        public_key = settings.PAYMOB_PUBLIC_KEY
        integration_ids = settings.PAYMOB_INTEGRATION_IDS
        if public_key is None or not integration_ids:
            msg = "PAYMOB_PUBLIC_KEY / PAYMOB_INTEGRATION_IDS are not set"
            raise GatewayResponseError(msg)
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/v1/intention/",
            headers=_headers(),
            json={
                "amount": to_minor_units(
                    amount=request.amount, currency=request.currency
                ),
                "currency": request.currency,
                "payment_methods": list(integration_ids),
                # Idempotent at Paymob AND echoed back as merchant_order_id.
                "special_reference": request.reference,
                "items": [],
                "billing_data": {
                    "first_name": request.customer_name or "Customer",
                    "last_name": "-",
                    "email": request.customer_email,
                    "phone_number": request.customer_phone or "+20000000000",
                },
                "notification_url": request.webhook_url,
                "redirection_url": request.redirect_url,
            },
            retry="connect-only",
        )
        payload = response.json()
        intention_id = payload.get("id")
        client_secret = payload.get("client_secret")
        if not intention_id or not client_secret:
            msg = f"paymob intention response missing id/client_secret: {payload}"
            raise GatewayResponseError(msg)
        checkout_url = (
            f"{_BASE}/unifiedcheckout/"
            f"?publicKey={public_key}&clientSecret={client_secret}"
        )
        return CheckoutSession(
            charge_id=str(intention_id), checkout_url=checkout_url, raw=payload
        )

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> WebhookEvent:
        posted = params.get("hmac", "")
        if not posted:
            msg = "missing hmac query parameter"
            raise WebhookVerificationError(msg)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            msg = "webhook body is not JSON"
            raise WebhookVerificationError(msg) from exc
        obj = payload.get("obj", {})
        if not hmac.compare_digest(_expected_hmac(obj), posted.lower()):
            msg = "hmac mismatch"
            raise WebhookVerificationError(msg)
        success = obj.get("success") is True
        return WebhookEvent(
            reference=str(obj.get("order", {}).get("merchant_order_id", "")),
            transaction_id=str(obj.get("id", "")),
            is_paid=success,
            status="success" if success else "failed",
            raw=payload,
        )

    def fetch_status(self, *, charge_id: str, reference: str) -> ChargeStatus:
        # Inquiry by the reference WE planted (special_reference ->
        # merchant_order_id) - fixes the old template's NotImplementedError.
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/api/ecommerce/orders/transaction_inquiry",
            headers=_headers(),
            json={"merchant_order_id": reference},
            retry="transient",  # read-only inquiry
        )
        payload = response.json()
        success = payload.get("success") is True and not payload.get("is_refunded")
        return ChargeStatus(
            transaction_id=str(payload.get("id", "")),
            is_paid=success,
            status="success" if success else str(payload.get("pending", "")),
            raw=payload,
        )

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/api/acceptance/void_refund/refund",
            headers=_headers(),
            json={
                "transaction_id": transaction_id,
                "amount_cents": to_minor_units(amount=amount, currency=currency),
            },
            retry="connect-only",
        )
        payload = response.json()
        return RefundResult(ok=payload.get("success") is True, raw=payload)


def _expected_hmac(obj: dict[str, Any]) -> str:
    secret = settings.PAYMOB_HMAC_SECRET
    # Blank is checked alongside None on purpose: env.py already normalises a
    # blank secret to None, but a signature check must fail closed on its own
    # input rather than trust that something upstream sanitised it - signing
    # with a blank key yields a digest an attacker can compute too.
    if secret is None or not str(secret.get_secret_value()).strip():
        msg = "PAYMOB_HMAC_SECRET is not set"
        raise WebhookVerificationError(msg)
    concatenated = "".join(_field_value(obj, field) for field in _HMAC_FIELDS)
    return hmac.new(
        str(secret.get_secret_value()).encode(), concatenated.encode(), hashlib.sha512
    ).hexdigest()


def _field_value(obj: dict[str, Any], dotted: str) -> str:
    value: Any = obj
    for part in dotted.split("."):
        value = value.get(part, "") if isinstance(value, dict) else ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
