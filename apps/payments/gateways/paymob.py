"""Paymob (paymob.com) - EGP. Intention API + unified checkout.

Webhook verification: the transaction-processed callback carries an ``hmac``
query parameter = HMAC-SHA512 (hex, lowercase) over the concatenated values
of 20 documented transaction fields in lexicographical key order, keyed with
the dashboard HMAC secret (developers.paymob.com "HMAC Calculation").
Booleans serialize as ``true``/``false``. Card-token callbacks
(``type: TOKEN``, sent when the customer ticks "Save Card" on the hosted
page) use the SAME secret and query parameter but their OWN 8-field
concatenation - see ``_TOKEN_HMAC_FIELDS``.

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
from apps.payments.gateways.base import SavedCardData
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.base import WebhookEventKind
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

# Card-token (type TOKEN) callbacks sign a DIFFERENT, 8-field concatenation -
# also lexicographical, same secret, same ``?hmac=`` query parameter.
_TOKEN_HMAC_FIELDS = (
    "card_subtype",
    "created_at",
    "email",
    "id",
    "masked_pan",
    "merchant_id",
    "order_id",
    "token",
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
        body = self._intention_body(
            request=request, payment_methods=list(integration_ids)
        )
        if request.saved_card is not None:
            # One-click CIT: unified checkout shows the stored card (CVV
            # only). Live mode uses the dedicated Card-on-File integration;
            # test mode has none - the normal 3DS integration accepts
            # card_tokens there.
            cof_id = settings.PAYMOB_COF_INTEGRATION_ID
            if cof_id is not None:
                body["payment_methods"] = [cof_id]
            body["card_tokens"] = [request.saved_card.token]
        payload = self._post_intention(body)
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

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession:
        """MIT/MOTO: intention on the MOTO integration, then a server-side
        pay with the stored token - no customer interaction, no redirect.

        ``special_reference`` still rides the intention, so the transaction
        callback that follows links back to our Payment row and a crash
        between the two calls self-heals via the webhook.
        """
        moto_id = settings.PAYMOB_MOTO_INTEGRATION_ID
        if moto_id is None:
            msg = "PAYMOB_MOTO_INTEGRATION_ID is not set"
            raise GatewayResponseError(msg)
        ref = request.saved_card
        if ref is None:
            msg = "paymob saved-card charge without a card ref"
            raise GatewayResponseError(msg)
        intention = self._post_intention(
            self._intention_body(request=request, payment_methods=[moto_id])
        )
        intention_id = intention.get("id")
        keys = intention.get("payment_keys") or []
        payment_key = next(
            (k.get("key") for k in keys if k.get("integration") == moto_id),
            keys[0].get("key") if keys else None,
        )
        if not intention_id or not payment_key:
            msg = f"paymob intention response missing id/payment_keys: {intention}"
            raise GatewayResponseError(msg)
        pay_response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/api/acceptance/payments/pay",
            headers=_headers(),
            json={
                "source": {"identifier": ref.token, "subtype": "TOKEN"},
                "payment_token": payment_key,
            },
            retry="connect-only",
        )
        pay = pay_response.json()
        success = pay.get("success") is True
        return CheckoutSession(
            charge_id=str(intention_id),
            checkout_url="",
            raw={"intention": intention, "payment": pay},
            is_paid=success,
            status="success" if success else "failed",
            transaction_id=str(pay.get("id", "")),
        )

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> bool:
        """Local-only: Paymob documents no public token-delete endpoint."""
        return True

    def _intention_body(
        self, *, request: CheckoutRequest, payment_methods: list[int]
    ) -> dict[str, Any]:
        return {
            "amount": to_minor_units(amount=request.amount, currency=request.currency),
            "currency": request.currency,
            "payment_methods": payment_methods,
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
        }

    def _post_intention(self, body: dict[str, Any]) -> dict[str, Any]:
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/v1/intention/",
            headers=_headers(),
            json=body,
            retry="connect-only",
        )
        payload: dict[str, Any] = response.json()
        return payload

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
        if payload.get("type") == "TOKEN":
            expected = _expected_hmac(obj, fields=_TOKEN_HMAC_FIELDS)
            if not hmac.compare_digest(expected, posted.lower()):
                msg = "hmac mismatch"
                raise WebhookVerificationError(msg)
            return WebhookEvent(
                reference="",  # token callbacks carry no planted reference
                transaction_id="",
                is_paid=False,
                status="token",
                raw=payload,
                kind=WebhookEventKind.CARD_TOKEN,
                saved_card=SavedCardData(
                    token=str(obj.get("token", "")),
                    customer_id="",
                    agreement_id="",
                    brand=str(obj.get("card_subtype", "")),
                    last4=_last4(str(obj.get("masked_pan", ""))),
                    exp_month=None,
                    exp_year=None,
                    email=str(obj.get("email", "")),
                ),
            )
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


def _expected_hmac(
    obj: dict[str, Any], *, fields: tuple[str, ...] = _HMAC_FIELDS
) -> str:
    secret = settings.PAYMOB_HMAC_SECRET
    # Blank is checked alongside None on purpose: env.py already normalises a
    # blank secret to None, but a signature check must fail closed on its own
    # input rather than trust that something upstream sanitised it - signing
    # with a blank key yields a digest an attacker can compute too.
    if secret is None or not str(secret.get_secret_value()).strip():
        msg = "PAYMOB_HMAC_SECRET is not set"
        raise WebhookVerificationError(msg)
    concatenated = "".join(_field_value(obj, field) for field in fields)
    return hmac.new(
        str(secret.get_secret_value()).encode(), concatenated.encode(), hashlib.sha512
    ).hexdigest()


def _last4(masked_pan: str) -> str:
    """``xxxx-xxxx-xxxx-2346`` (or any masked shape) -> last four digits."""
    digits = "".join(char for char in masked_pan if char.isdigit())
    return digits[-4:]


def _field_value(obj: dict[str, Any], dotted: str) -> str:
    value: Any = obj
    for part in dotted.split("."):
        value = value.get(part, "") if isinstance(value, dict) else ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
