"""Paymob (paymob.com) - EGP. Intention API + unified checkout.

Docs: developers.paymob.com (Egypt doc set; the legacy 3-step
auth-token/order/payment-key flow is gone from it). Three credentials:

- secret key   -> ``Authorization: Token`` on intention / pay / refund;
- public key   -> only ever interpolated into the hosted-checkout URL;
- API key      -> minted into a one-hour ``auth_token`` (cached) because the
  transaction-inquiry endpoint still authenticates with that, in the body.

Plus the integration ids: the ordinary payment methods, the Card-on-File one
(one-click CIT with a stored token) and the MOTO one (server-side MIT). All
three are required to run Paymob at all - the constructor refuses a partial
configuration rather than routing a stored-card charge through an
integration that happens to accept it.

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

from apps.common.http import PROVIDER_TIMEOUT
from apps.common.http import OutboundStatusError
from apps.common.http import request_json
from apps.payments.gateways.base import CardTokenEvent
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import GatewayConfigurationError
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import RefundStatus
from apps.payments.gateways.base import SavedCardData
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
    and anything still pending. Flags are booleans that Paymob omits when
    false, so absence reads as false by the provider's own convention.
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

    def __init__(self) -> None:
        integration_ids = settings.PAYMOB_INTEGRATION_IDS
        if not integration_ids:
            msg = "Paymob is not configured (PAYMOB_INTEGRATION_IDS is empty)"
            raise GatewayConfigurationError(msg)
        cof_id = settings.PAYMOB_COF_INTEGRATION_ID
        moto_id = settings.PAYMOB_MOTO_INTEGRATION_ID
        if cof_id is None or moto_id is None:
            msg = (
                "PAYMOB_COF_INTEGRATION_ID and PAYMOB_MOTO_INTEGRATION_ID are "
                "required with Paymob"
            )
            raise GatewayConfigurationError(msg)
        secret_key = settings.PAYMOB_SECRET_KEY
        public_key = settings.PAYMOB_PUBLIC_KEY
        api_key = settings.PAYMOB_API_KEY
        hmac_secret = settings.PAYMOB_HMAC_SECRET
        if secret_key is None or public_key is None or api_key is None:
            msg = "PAYMOB_SECRET_KEY, PAYMOB_PUBLIC_KEY and PAYMOB_API_KEY are required"
            raise GatewayConfigurationError(msg)
        # Blank is checked alongside None on purpose: env.py already
        # normalises a blank secret to None, but signature verification must
        # fail closed on its own input - signing with a blank key yields a
        # digest an attacker can compute too.
        if hmac_secret is None or not str(hmac_secret.get_secret_value()).strip():
            msg = "PAYMOB_HMAC_SECRET is not set"
            raise GatewayConfigurationError(msg)
        self._integration_ids = list(integration_ids)
        self._cof_id = cof_id
        self._moto_id = moto_id
        self._secret_key = str(secret_key.get_secret_value())
        self._public_key = public_key
        self._api_key = str(api_key.get_secret_value())
        self._hmac_secret = str(hmac_secret.get_secret_value())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._secret_key}"}

    def _auth_token(self) -> str:
        """One-hour auth token for the endpoints that still take it in the
        body (transaction inquiry). Cached for 50 minutes."""
        cached: str | None = cache.get(_AUTH_TOKEN_CACHE_KEY)
        if cached:
            return cached
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/api/auth/tokens",
            json={"api_key": self._api_key},
            timeout=PROVIDER_TIMEOUT,
            retry="transient",  # minting a token is idempotent
        )
        token = _str(response.json(), "token")
        cache.set(_AUTH_TOKEN_CACHE_KEY, token, _AUTH_TOKEN_TTL_SECONDS)
        return token

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        body = self._intention_body(
            request=request, payment_methods=self._integration_ids
        )
        if request.saved_card is not None:
            # One-click CIT: unified checkout shows the stored card (CVV
            # only) on the Card-on-File integration.
            body["payment_methods"] = [self._cof_id]
            body["card_tokens"] = [request.saved_card.token]
        payload = self._post_intention(body)
        _id(payload)  # shape check: an intention without an id is broken
        client_secret = _str(payload, "client_secret")
        return CheckoutSession(
            charge_id=_order_id(payload),
            checkout_url=(
                f"{_CHECKOUT_BASE}?publicKey={self._public_key}"
                f"&clientSecret={client_secret}"
            ),
            raw=payload,
            outcome=None,
        )

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession:
        """MIT/MOTO: intention on the MOTO integration, then a server-side
        pay with the stored token - no customer interaction, no redirect.

        ``special_reference`` still rides the intention, so the transaction
        callback that follows links back to our Payment row and a crash
        between the two calls self-heals via the webhook.
        """
        ref = request.saved_card
        if ref is None:
            msg = "paymob saved-card charge without a card ref"
            raise GatewayResponseError(msg)
        intention = self._post_intention(
            self._intention_body(request=request, payment_methods=[self._moto_id])
        )
        _id(intention)  # shape check: an intention without an id is broken
        payment_key = next(
            (
                key.get("key")
                for key in intention.get("payment_keys") or []
                if isinstance(key, dict) and key.get("integration") == self._moto_id
            ),
            None,
        )
        if not isinstance(payment_key, str) or not payment_key:
            msg = (
                f"paymob intention carries no payment key for the MOTO "
                f"integration {self._moto_id}: {intention}"
            )
            raise GatewayResponseError(msg)
        pay_response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/api/acceptance/payments/pay",
            headers=self._headers(),
            json={
                "source": {"identifier": ref.token, "subtype": "TOKEN"},
                "payment_token": payment_key,
            },
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",
        )
        pay = pay_response.json()
        event = _transaction_event(pay, reference=request.reference)
        return CheckoutSession(
            charge_id=_order_id(intention),
            checkout_url="",
            raw={"intention": intention, "payment": pay},
            # Still pending: the webhook / reconcile sweep settles it.
            outcome=None if event.is_pending else event,
        )

    def _intention_body(
        self, *, request: CheckoutRequest, payment_methods: list[int]
    ) -> dict[str, Any]:
        # first/last/email/phone are the fields Paymob validates; the service
        # guarantees a full name and a phone, so nothing is made up here.
        first_name, _, last_name = request.customer_name.strip().partition(" ")
        return {
            "amount": to_minor_units(amount=request.amount),
            "currency": request.currency,
            "payment_methods": payment_methods,
            # Idempotent at Paymob AND echoed back as merchant_order_id.
            "special_reference": request.reference,
            "expiration": INTENTION_EXPIRATION_SECONDS,
            "items": [],
            "billing_data": {
                "first_name": first_name,
                "last_name": last_name.strip(),
                "email": request.customer_email,
                "phone_number": request.customer_phone,
            },
            "notification_url": request.webhook_url,
            "redirection_url": request.redirect_url,
        }

    def _post_intention(self, body: dict[str, Any]) -> dict[str, Any]:
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/v1/intention/",
            headers=self._headers(),
            json=body,
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",
        )
        payload = response.json()
        if not isinstance(payload, dict):
            msg = f"paymob intention response is not an object: {payload}"
            raise GatewayResponseError(msg)
        return payload

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> PaymentEvent | CardTokenEvent:
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
        obj = payload.get("obj")
        if not isinstance(obj, dict):
            msg = "webhook obj is not a JSON object"
            raise WebhookVerificationError(msg)
        if payload.get("type") == "TOKEN":
            expected = self._expected_hmac(obj, fields=_TOKEN_HMAC_FIELDS)
            if not hmac.compare_digest(expected, posted.lower()):
                msg = "hmac mismatch"
                raise WebhookVerificationError(msg)
            return CardTokenEvent(
                saved_card=SavedCardData(
                    token=_str(obj, "token"),
                    customer_id="",
                    agreement_id="",
                    brand=_str(obj, "card_subtype"),
                    last4=_last4(_str(obj, "masked_pan")),
                    # Present since the Aug-2026 token payload ("01"/"38");
                    # outside the HMAC, so display-only data.
                    exp_month=_expiry_month(obj),
                    exp_year=_expiry_year(obj),
                    email=_str(obj, "email"),
                ),
                raw=payload,
            )
        if not hmac.compare_digest(
            self._expected_hmac(obj, fields=_HMAC_FIELDS), posted.lower()
        ):
            msg = "hmac mismatch"
            raise WebhookVerificationError(msg)
        order = _order(obj)
        # Paymob's own callback sample carries ``merchant_order_id: null`` for
        # transactions not created through an intention of ours - "" then
        # resolves to no Payment row (404), which is the right answer.
        merchant_order_id = order.get("merchant_order_id")
        if merchant_order_id is not None and not isinstance(merchant_order_id, str):
            msg = f"paymob merchant_order_id is not a string: {merchant_order_id!r}"
            raise GatewayResponseError(msg)
        return _transaction_event(obj, reference=merchant_order_id or "", raw=payload)

    def fetch_status(self, *, charge_id: str, reference: str) -> PaymentEvent | None:
        """Inquiry by the reference WE planted (special_reference ->
        merchant_order_id). Returns the LAST transaction on the order; an
        order nobody paid yet has none, which Paymob reports as a 404 - that
        is "still pending" (None), not a provider outage.
        """
        try:
            response = request_json(
                service="paymob",
                method="POST",
                url=f"{_BASE}/api/ecommerce/orders/transaction_inquiry",
                json={
                    "auth_token": self._auth_token(),
                    "merchant_order_id": reference,
                },
                timeout=PROVIDER_TIMEOUT,
                retry="transient",  # read-only inquiry
            )
        except OutboundStatusError as exc:
            if exc.status_code == 404:
                return None
            raise
        return _transaction_event(response.json(), reference=reference)

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        response = request_json(
            service="paymob",
            method="POST",
            url=f"{_BASE}/api/acceptance/void_refund/refund",
            headers=self._headers(),
            json={
                "transaction_id": transaction_id,
                "amount_cents": to_minor_units(amount=amount),
            },
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",
        )
        return _refund_result(response.json())

    def fetch_refund(self, *, refund_id: str) -> RefundResult:
        """The refund child transaction as Paymob holds it (Retrieve
        Transaction, Bearer auth token)."""
        response = request_json(
            service="paymob",
            method="GET",
            url=f"{_BASE}/api/acceptance/transactions/{refund_id}",
            headers={"Authorization": f"Bearer {self._auth_token()}"},
            timeout=PROVIDER_TIMEOUT,
            retry="transient",  # read-only
        )
        return _refund_result(response.json())

    def _expected_hmac(self, obj: dict[str, Any], *, fields: tuple[str, ...]) -> str:
        concatenated = "".join(_field_value(obj, field) for field in fields)
        return hmac.new(
            self._hmac_secret.encode(), concatenated.encode(), hashlib.sha512
        ).hexdigest()


def _transaction_event(
    obj: Any, *, reference: str, raw: dict[str, Any] | None = None
) -> PaymentEvent:
    """One PaymentEvent from a Paymob transaction object (callback ``obj``,
    inquiry response, or the pay response). ``raw`` is the full callback
    envelope when there is one; otherwise the transaction itself."""
    if not isinstance(obj, dict):
        msg = f"paymob transaction is not an object: {obj}"
        raise GatewayResponseError(msg)
    outcome = _outcome(obj)
    is_child = obj.get("has_parent_transaction") is True
    return PaymentEvent(
        reference=reference,
        # order.id is HMAC-signed on every callback (merchant_order_id is
        # not) and is the intention's order - the identity the row keeps.
        charge_id=str(_id(_order(obj))),
        # A refund/void/capture child must not replace the settled
        # transaction id the refund path targets.
        transaction_id="" if is_child else str(_id(obj)),
        is_paid=outcome.is_paid,
        is_pending=outcome.is_pending,
        status=outcome.status,
        # amount_cents/currency are HMAC-signed on a callback. On a child
        # they describe the child (a partial refund's amount) - the event is
        # pending, so the service never cross-checks them against the row.
        amount_minor=_int(obj, "amount_cents"),
        currency=_str(obj, "currency"),
        saved_card=None,  # Paymob vaults cards via its own TOKEN callback
        raw=raw if raw is not None else obj,
    )


def _order(obj: dict[str, Any]) -> dict[str, Any]:
    order = obj.get("order")
    if not isinstance(order, dict):
        msg = f"paymob transaction lacks an order object: {obj}"
        raise GatewayResponseError(msg)
    return order


def _order_id(intention: dict[str, Any]) -> str:
    """The order an intention created (``intention_order_id``) - what every
    signed transaction callback and inquiry reports as ``order.id``."""
    value = intention.get("intention_order_id")
    if isinstance(value, bool) or not isinstance(value, int | str) or value == "":
        msg = f"paymob intention lacks intention_order_id: {intention}"
        raise GatewayResponseError(msg)
    return str(value)


def _refund_result(payload: Any) -> RefundResult:
    """A refund transaction object -> outcome. ``success`` with
    ``pending=false`` is done; ``pending`` is still with the acquirer;
    anything else is a rejection."""
    if not isinstance(payload, dict):
        msg = f"paymob refund response is not an object: {payload}"
        raise GatewayResponseError(msg)
    if payload.get("pending") is True:
        status = RefundStatus.PENDING
    elif payload.get("success") is True:
        status = RefundStatus.SUCCEEDED
    else:
        status = RefundStatus.FAILED
    return RefundResult(status=status, refund_id=str(_id(payload)), raw=payload)


def _last4(masked_pan: str) -> str:
    """``xxxx-xxxx-xxxx-2346`` (or any masked shape) -> last four digits."""
    digits = "".join(char for char in masked_pan if char.isdigit())
    if len(digits) < 4:
        msg = f"paymob masked_pan carries fewer than four digits: {masked_pan!r}"
        raise GatewayResponseError(msg)
    return digits[-4:]


def _id(obj: dict[str, Any]) -> int | str:
    """Paymob ids are integers on transactions and strings on intentions."""
    value = obj.get("id")
    if isinstance(value, bool) or not isinstance(value, int | str) or value == "":
        msg = f"paymob payload id missing or of the wrong type: {value!r}"
        raise GatewayResponseError(msg)
    return value


def _int(obj: dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"paymob payload field {key!r} missing or not an integer: {value!r}"
        raise GatewayResponseError(msg)
    return value


def _str(obj: Any, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str) or not value:
        msg = f"paymob payload field {key!r} missing or not a string: {value!r}"
        raise GatewayResponseError(msg)
    return value


def _expiry_month(obj: dict[str, Any]) -> int | None:
    """Absent on token payloads before Aug-2026 -> None; present but not a
    calendar month -> the payload is broken."""
    if "expiry_month" not in obj:
        return None
    text = str(obj["expiry_month"]).strip()
    if not text.isdigit() or not 1 <= int(text) <= 12:
        msg = f"paymob expiry_month is not a month: {obj['expiry_month']!r}"
        raise GatewayResponseError(msg)
    return int(text)


def _expiry_year(obj: dict[str, Any]) -> int | None:
    if "expiry_year" not in obj:
        return None
    text = str(obj["expiry_year"]).strip()
    if not text.isdigit():
        msg = f"paymob expiry_year is not a year: {obj['expiry_year']!r}"
        raise GatewayResponseError(msg)
    year = int(text)
    return year + 2000 if year < 100 else year


def _field_value(obj: dict[str, Any], dotted: str) -> str:
    """The value of one HMAC field; a missing one is a verification failure
    that names it - Paymob signs every documented field, so a payload
    without one was not signed by Paymob."""
    value: Any = obj
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            msg = f"webhook payload lacks signed field {dotted!r}"
            raise WebhookVerificationError(msg)
        value = value[part]
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
