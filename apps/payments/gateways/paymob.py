"""Paymob (paymob.com) - EGP. Intention API + unified checkout.

Docs: developers.paymob.com (Egypt doc set; the legacy 3-step
auth-token/order/payment-key flow is gone from it). Three credentials:

- secret key   -> ``Authorization: Token`` on intention / pay / refund;
- public key   -> only ever interpolated into the hosted-checkout URL;
- API key      -> minted into a one-hour ``auth_token`` (cached) because the
  transaction-inquiry endpoint still authenticates with that, in the body.

Webhook verification: the transaction-processed callback carries an ``hmac``
query parameter = HMAC-SHA512 (hex, lowercase) over the concatenated values
of 20 documented transaction fields in the documented order, keyed with the
dashboard HMAC secret. Booleans serialize as ``true``/``false``. Card-token
callbacks (``type: TOKEN``, sent when the customer ticks "Save Card" on the
hosted page) use the SAME secret and query parameter but their OWN 8-field
concatenation - see ``_TOKEN_HMAC_FIELDS``.

Outcome derivation follows the docs' own rule (``_outcome``): ``success``
with ``pending=false`` is paid, ``pending=true`` is still in flight (the
customer is on the bank's OTP page, or a kiosk reference awaits cash),
``success=false`` with ``pending=false`` is declined. Refunds, voids and
captures arrive as CHILD transactions (``has_parent_transaction``) on the
same order and must never transition the Payment row.

``special_reference`` (= our idempotency key) makes charge creation
idempotent at Paymob AND comes back as ``order.merchant_order_id`` in the
callback - the old template generated a fresh uuid per attempt, defeating
both.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache

from apps.common.http import OutboundStatusError
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
#: Unified Checkout moved to a per-region host (docs, July 2026); the old
#: ``accept.paymob.com/unifiedcheckout/`` path only survives as a 302.
_CHECKOUT_BASE = "https://eg.checkout.paymob.com/"
#: Intention lifetime in seconds - the documented maximum. constants.
#: PENDING_EXPIRY must stay above this so the reconcile sweep never expires
#: a checkout the customer can still complete.
INTENTION_EXPIRATION_SECONDS = 3600
_AUTH_TOKEN_CACHE_KEY = "paymob:auth_token"  # noqa: S105 - cache key, not a secret
#: Paymob auth tokens live one hour; refresh well before that.
_AUTH_TOKEN_TTL_SECONDS = 50 * 60

# The documented field list, in the DOCUMENTED order (the docs say "sorted
# lexicographically" but their own list is not - the list is authoritative);
# dots reach into nested objects.
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
# same secret, same ``?hmac=`` query parameter.
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


def _auth_token() -> str:
    """One-hour auth token for the endpoints that still take it in the body
    (transaction inquiry). Cached for 50 minutes; a missing API key is loud."""
    api_key = settings.PAYMOB_API_KEY
    if api_key is None:
        msg = "PAYMOB_API_KEY is not set"
        raise GatewayResponseError(msg)
    cached = cache.get(_AUTH_TOKEN_CACHE_KEY)
    if isinstance(cached, str) and cached:
        return cached
    response = request_json(
        service="paymob",
        method="POST",
        url=f"{_BASE}/api/auth/tokens",
        json={"api_key": str(api_key.get_secret_value())},
        retry="transient",  # minting a token is idempotent
    )
    token = response.json().get("token")
    if not token:
        msg = "paymob auth token response missing token"
        raise GatewayResponseError(msg)
    cache.set(_AUTH_TOKEN_CACHE_KEY, str(token), _AUTH_TOKEN_TTL_SECONDS)
    return str(token)


@dataclass(frozen=True, slots=True)
class _Outcome:
    is_paid: bool
    is_pending: bool  # informational - never transitions the row
    status: str


def _outcome(obj: Mapping[str, Any]) -> _Outcome:
    """Derive one outcome from Paymob's transaction flags (docs, "Transaction
    callbacks"). Only two shapes ever transition a Payment: a clean
    ``success`` with ``pending=false`` (paid) and a ``success=false`` with
    ``pending=false`` (declined). Everything else is recorded, not applied:
    child transactions of a refund/void/capture, a parent re-announced as
    voided/refunded, an authorization awaiting capture (we never capture),
    and anything still pending.
    """
    if obj.get("has_parent_transaction") is True:
        if obj.get("is_refund") is True:
            status = "refund"
        elif obj.get("is_void") is True:
            status = "void"
        elif obj.get("is_capture") is True:
            status = "capture"
        else:
            status = "action"
        return _Outcome(is_paid=False, is_pending=True, status=status)
    if obj.get("is_refunded") is True:
        return _Outcome(is_paid=False, is_pending=True, status="refunded")
    if obj.get("is_voided") is True:
        return _Outcome(is_paid=False, is_pending=True, status="voided")
    if obj.get("pending") is True:
        return _Outcome(is_paid=False, is_pending=True, status="pending")
    success = obj.get("success") is True
    if success and obj.get("is_auth") is True and obj.get("is_capture") is not True:
        return _Outcome(is_paid=False, is_pending=True, status="authorized")
    return _Outcome(
        is_paid=success, is_pending=False, status="success" if success else "failed"
    )


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
        return self._hosted_session(self._post_intention(body))

    def _hosted_session(self, payload: dict[str, Any]) -> CheckoutSession:
        public_key = settings.PAYMOB_PUBLIC_KEY
        if public_key is None:
            msg = "PAYMOB_PUBLIC_KEY is not set"
            raise GatewayResponseError(msg)
        intention_id = payload.get("id")
        client_secret = payload.get("client_secret")
        if not intention_id or not client_secret:
            msg = f"paymob intention response missing id/client_secret: {payload}"
            raise GatewayResponseError(msg)
        checkout_url = (
            f"{_CHECKOUT_BASE}?publicKey={public_key}&clientSecret={client_secret}"
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
        outcome = _outcome(pay)
        return CheckoutSession(
            charge_id=str(intention_id),
            checkout_url="",
            raw={"intention": intention, "payment": pay},
            is_paid=outcome.is_paid,
            # "" = still pending: the webhook / reconcile sweep settles it.
            status="" if outcome.is_pending else outcome.status,
            transaction_id=str(pay.get("id", "")),
        )

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> bool:
        """Local-only: Paymob documents no public token-delete endpoint."""
        return True

    def saved_card_fingerprint(self, *, saved_card: SavedCardRef) -> str:
        """Paymob exposes no card fingerprint; tokens dedupe on their own."""
        return ""

    def _intention_body(
        self, *, request: CheckoutRequest, payment_methods: list[int]
    ) -> dict[str, Any]:
        first_name, _, last_name = request.customer_name.strip().partition(" ")
        return {
            "amount": to_minor_units(amount=request.amount, currency=request.currency),
            "currency": request.currency,
            "payment_methods": payment_methods,
            # Idempotent at Paymob AND echoed back as merchant_order_id.
            "special_reference": request.reference,
            "expiration": INTENTION_EXPIRATION_SECONDS,
            "items": [],
            # first/last/email/phone are the fields Paymob validates (phone
            # is rejected when missing despite the schema calling it optional).
            "billing_data": {
                "first_name": first_name or "Customer",
                "last_name": last_name.strip() or "-",
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
        if not isinstance(payload, dict):
            msg = "webhook body is not a JSON object"
            raise WebhookVerificationError(msg)
        obj = payload.get("obj", {})
        if not isinstance(obj, dict):
            obj = {}
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
                    # Present since the Aug-2026 token payload ("01"/"38");
                    # outside the HMAC, so display-only data.
                    exp_month=_expiry_month(obj.get("expiry_month")),
                    exp_year=_expiry_year(obj.get("expiry_year")),
                    email=str(obj.get("email", "")),
                ),
            )
        if not hmac.compare_digest(_expected_hmac(obj), posted.lower()):
            msg = "hmac mismatch"
            raise WebhookVerificationError(msg)
        outcome = _outcome(obj)
        order = obj.get("order")
        reference = (
            str(order.get("merchant_order_id") or "") if isinstance(order, dict) else ""
        )
        is_child = obj.get("has_parent_transaction") is True
        return WebhookEvent(
            reference=reference,
            # A refund/void/capture child must not replace the settled
            # transaction id the refund path targets.
            transaction_id="" if is_child else str(obj.get("id", "")),
            is_paid=outcome.is_paid,
            status=outcome.status,
            raw=payload,
            is_pending=outcome.is_pending,
            # amount_cents/currency are HMAC-signed on the parent; a child's
            # amount is the refund/capture amount, not the payment's.
            amount_minor=None if is_child else _int_or_none(obj.get("amount_cents")),
            currency="" if is_child else str(obj.get("currency", "")),
        )

    def fetch_status(self, *, charge_id: str, reference: str) -> ChargeStatus:
        """Inquiry by the reference WE planted (special_reference ->
        merchant_order_id). Returns the LAST transaction on the order; an
        order nobody paid yet has none, which Paymob reports as a 404 - that
        is "still pending", not a provider outage.
        """
        try:
            response = request_json(
                service="paymob",
                method="POST",
                url=f"{_BASE}/api/ecommerce/orders/transaction_inquiry",
                json={"auth_token": _auth_token(), "merchant_order_id": reference},
                retry="transient",  # read-only inquiry
            )
        except OutboundStatusError as exc:
            if exc.status_code == 404:
                return ChargeStatus(
                    transaction_id="",
                    is_paid=False,
                    status="no_transaction",
                    raw={"status_code": exc.status_code, "body": exc.body},
                    is_pending=True,
                )
            raise
        payload = response.json()
        outcome = _outcome(payload)
        return ChargeStatus(
            transaction_id=str(payload.get("id", "")),
            is_paid=outcome.is_paid,
            status=outcome.status,
            raw=payload,
            is_pending=outcome.is_pending,
            amount_minor=_int_or_none(payload.get("amount_cents")),
            currency=str(payload.get("currency", "")),
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
    return digits[-4:] if len(digits) >= 4 else ""


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _expiry_month(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    month = int(text)
    return month if 1 <= month <= 12 else None


def _expiry_year(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    year = int(text)
    return year + 2000 if year < 100 else year


def _field_value(obj: dict[str, Any], dotted: str) -> str:
    value: Any = obj
    for part in dotted.split("."):
        value = value.get(part, "") if isinstance(value, dict) else ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
