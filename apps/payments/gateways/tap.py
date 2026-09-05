"""Tap (tap.company) - SAR. Charges API v2, Bearer secret key.

Webhook verification: Tap sends a ``hashstring`` header = HMAC-SHA256 over
the documented concatenation
``x_id{id}x_amount{amount}x_currency{currency}x_gateway_reference{...}
x_payment_reference{...}x_status{status}x_created{created}``
keyed with the SAME secret API key (developers.tap.company/docs/webhook).
Amount is formatted with the currency's decimal places.

Every charge payload (create response, webhook, status inquiry) goes through
ONE allowlist parser (``_parse_charge``): a field that is missing or of the
wrong type is a ``GatewayResponseError`` naming it, never a blank default.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import phonenumbers
from django.conf import settings

from apps.common.http import PROVIDER_TIMEOUT
from apps.common.http import request_json
from apps.payments.gateways.base import MINOR_UNITS
from apps.payments.gateways.base import CardTokenEvent
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import GatewayConfigurationError
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import RefundStatus
from apps.payments.gateways.base import SavedCardData
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.base import to_minor_units

_BASE = "https://api.tap.company/v2"
_PAID_STATUS = "CAPTURED"
# Statuses that mean "not settled yet" - the row stays PENDING for the
# webhook / payment_verify / the reconcile sweep rather than being declared
# final here.
_PENDING_STATUSES = {"INITIATED", "IN_PROGRESS"}
# Refund object statuses (developers.tap.company/reference/refunds). Only
# REFUNDED is completion; ACCEPTED/PENDING mean the acquirer still has it,
# and anything unknown is treated the same way - never as done.
_REFUND_DONE = {"REFUNDED"}
_REFUND_FAILED = {"FAILED", "DECLINED", "CANCELLED", "REJECTED", "ERROR"}


class TapGateway:
    name = "tap"

    def __init__(self) -> None:
        key = settings.TAP_SECRET_KEY
        if key is None:
            msg = "TAP_SECRET_KEY is not set"
            raise GatewayConfigurationError(msg)
        self._secret = str(key.get_secret_value())

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._secret}"}

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        if request.saved_card is not None:  # one-click CIT with a stored card
            return self._charge_saved_card(request=request, customer_initiated=True)
        response = request_json(
            service="tap",
            method="POST",
            url=f"{_BASE}/charges/",
            headers=self._headers(),
            json={
                "amount": str(request.amount),
                "currency": request.currency,
                "customer_initiated": True,
                "threeDSecure": True,  # saving requires a 3DS-verified holder
                "save_card": True,  # every new-card checkout vaults the card
                "description": request.description,
                "reference": {
                    # The planted reference - webhooks echo it back.
                    "transaction": request.reference,
                    "order": request.reference,
                },
                "source": {"id": "src_all"},
                "post": {"url": request.webhook_url},
                "redirect": {"url": request.redirect_url},
                "customer": _customer(request),
            },
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",  # POST: only provably-unsent requests retry
        )
        charge = _parse_charge(response.json())
        if charge.redirect_url is None:
            msg = f"tap charge response missing transaction.url: {charge.raw}"
            raise GatewayResponseError(msg)
        return CheckoutSession(
            charge_id=charge.id,
            checkout_url=charge.redirect_url,
            raw=charge.raw,
            outcome=None,
        )

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession:
        return self._charge_saved_card(request=request, customer_initiated=False)

    def _charge_saved_card(
        self, *, request: CheckoutRequest, customer_initiated: bool
    ) -> CheckoutSession:
        """Token then charge - a stored card_id is never a valid charge source.

        CIT keeps 3DS on (the issuer may challenge: the response then carries
        ``transaction.url`` and the webhook settles it); MIT goes non-3DS,
        which Tap only accepts alongside a valid payment_agreement id.
        """
        ref = request.saved_card
        if ref is None:
            msg = "tap saved-card charge without a card ref"
            raise GatewayResponseError(msg)
        if not customer_initiated and not ref.agreement_id:
            msg = "tap MIT charge requires a payment_agreement id"
            raise GatewayResponseError(msg)
        token_response = request_json(
            service="tap",
            method="POST",
            url=f"{_BASE}/tokens",
            headers=self._headers(),
            json={"saved_card": {"card_id": ref.token, "customer_id": ref.customer_id}},
            timeout=PROVIDER_TIMEOUT,
            # Token creation moves no money; a duplicate single-use token just
            # expires unused in 5 minutes.
            retry="transient",
        )
        token_id = _str(token_response.json(), "id")
        body: dict[str, Any] = {
            "amount": str(request.amount),
            "currency": request.currency,
            "customer_initiated": customer_initiated,
            "threeDSecure": customer_initiated,
            "save_card": False,
            "description": request.description,
            "reference": {
                "transaction": request.reference,
                "order": request.reference,
            },
            "source": {"id": token_id, "on_file": True},
            "customer": {"id": ref.customer_id},
            # ALWAYS sent, even for synchronous outcomes: a crash between the
            # charge and our row update self-heals via the webhook.
            "post": {"url": request.webhook_url},
            "redirect": {"url": request.redirect_url},
        }
        if ref.agreement_id:
            body["payment_agreement"] = {"id": ref.agreement_id}
        response = request_json(
            service="tap",
            method="POST",
            url=f"{_BASE}/charges/",
            headers=self._headers(),
            json=body,
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",
        )
        charge = _parse_charge(response.json())
        if charge.redirect_url is not None:  # 3DS challenge: redirect + webhook
            return CheckoutSession(
                charge_id=charge.id,
                checkout_url=charge.redirect_url,
                raw=charge.raw,
                outcome=None,
            )
        event = charge.event(reference=request.reference)
        return CheckoutSession(
            charge_id=charge.id,
            checkout_url="",
            raw=charge.raw,
            # Still in flight (no redirect, not final): the webhook settles it.
            outcome=None if event.is_pending else event,
        )

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> None:
        response = request_json(
            service="tap",
            method="DELETE",
            url=f"{_BASE}/card/{saved_card.customer_id}/{saved_card.token}",
            headers=self._headers(),
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",
        )
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("deleted") is not True:
            msg = f"tap did not confirm the card deletion: {payload}"
            raise GatewayResponseError(msg)

    def saved_card_fingerprint(self, *, saved_card: SavedCardRef) -> str:
        """Tap's fingerprint for a vaulted card, read from the Card API - the
        charge/webhook ``card`` object does not document one."""
        response = request_json(
            service="tap",
            method="GET",
            url=f"{_BASE}/card/{saved_card.customer_id}/{saved_card.token}",
            headers=self._headers(),
            timeout=PROVIDER_TIMEOUT,
            retry="transient",  # GET is idempotent
        )
        payload = response.json()
        if not isinstance(payload, dict):
            msg = f"tap card response is not an object: {payload}"
            raise GatewayResponseError(msg)
        return _str(payload, "fingerprint")

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> PaymentEvent | CardTokenEvent:
        posted = headers.get("hashstring", "")
        if not posted:
            msg = "missing hashstring header"
            raise WebhookVerificationError(msg)
        try:
            payload = json.loads(body)
        except ValueError as exc:
            msg = "webhook body is not JSON"
            raise WebhookVerificationError(msg) from exc
        if not isinstance(payload, dict):
            msg = "webhook body is not a JSON object"
            raise WebhookVerificationError(msg)
        if not hmac.compare_digest(self._expected_hashstring(payload), posted):
            msg = "hashstring mismatch"
            raise WebhookVerificationError(msg)
        charge = _parse_charge(payload)
        return charge.event(reference=_planted_reference(payload))

    def fetch_status(self, *, charge_id: str, reference: str) -> PaymentEvent | None:
        """The charge as Tap holds it. The reference on the event is the one
        Tap echoes back, not the one asked for: the service compares the two,
        which is what makes an authenticated lookup a binding proof. Tap has
        no lookup by merchant reference, so a row that never learned its
        charge id has nothing to ask for (None = still pending)."""
        if not charge_id:
            return None
        response = request_json(
            service="tap",
            method="GET",
            url=f"{_BASE}/charges/{charge_id}",
            headers=self._headers(),
            timeout=PROVIDER_TIMEOUT,
            retry="transient",  # GET is idempotent
        )
        payload = response.json()
        # A Tap charge always exists once created - never None here.
        return _parse_charge(payload).event(reference=_planted_reference(payload))

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        response = request_json(
            service="tap",
            method="POST",
            url=f"{_BASE}/refunds/",
            headers=self._headers(),
            json={
                "charge_id": transaction_id,
                "amount": str(amount),
                "currency": currency,
                "reason": "requested_by_customer",
            },
            timeout=PROVIDER_TIMEOUT,
            retry="connect-only",
        )
        return _refund_result(response.json())

    def fetch_refund(self, *, refund_id: str) -> RefundResult:
        response = request_json(
            service="tap",
            method="GET",
            url=f"{_BASE}/refunds/{refund_id}",
            headers=self._headers(),
            timeout=PROVIDER_TIMEOUT,
            retry="transient",  # GET is idempotent
        )
        return _refund_result(response.json())

    def _expected_hashstring(self, payload: dict[str, Any]) -> str:
        """The documented concatenation. Every field is indexed: a payload
        that lacks one cannot have been signed by Tap, and the failure names
        the field instead of hashing a blank in its place."""
        reference = _signed(payload, "reference")
        transaction = _signed(payload, "transaction")
        if not isinstance(reference, dict) or not isinstance(transaction, dict):
            msg = "webhook reference/transaction are not objects"
            raise WebhookVerificationError(msg)
        concatenated = (
            f"x_id{_signed(payload, 'id')}"
            f"x_amount{_format_amount(_signed(payload, 'amount'))}"
            f"x_currency{_signed(payload, 'currency')}"
            f"x_gateway_reference{_signed(reference, 'gateway')}"
            f"x_payment_reference{_signed(reference, 'payment')}"
            f"x_status{_signed(payload, 'status')}"
            f"x_created{_signed(transaction, 'created')}"
        )
        return hmac.new(
            self._secret.encode(), concatenated.encode(), hashlib.sha256
        ).hexdigest()


def _customer(request: CheckoutRequest) -> dict[str, Any]:
    parsed = phonenumbers.parse(request.customer_phone)
    customer: dict[str, Any] = {
        "first_name": request.customer_name,
        "email": request.customer_email,
        "phone": {
            "country_code": parsed.country_code,
            "number": parsed.national_number,
        },
    }
    if request.customer_id:  # file the charge (and its card) under this customer
        customer["id"] = request.customer_id
    return customer


@dataclass(frozen=True, slots=True)
class _Charge:
    """The allowlisted view of a Tap charge object."""

    id: str
    status: str
    amount_minor: int
    currency: str
    redirect_url: str | None  # transaction.url - present while a redirect is due
    saved_card: SavedCardData | None
    raw: dict[str, Any]

    def event(self, *, reference: str) -> PaymentEvent:
        status = self.status.upper()
        return PaymentEvent(
            reference=reference,
            charge_id=self.id,  # signed (x_id) on every webhook
            transaction_id=self.id,  # Tap's charge id IS the settled txn id
            is_paid=status == _PAID_STATUS,
            is_pending=status in _PENDING_STATUSES,
            status=self.status,
            amount_minor=self.amount_minor,
            currency=self.currency,
            saved_card=self.saved_card,
            raw=self.raw,
        )


def _parse_charge(payload: Any) -> _Charge:
    if not isinstance(payload, dict):
        msg = f"tap charge payload is not an object: {payload}"
        raise GatewayResponseError(msg)
    amount = payload.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, int | float | str):
        msg = f"tap charge amount is not a number: {amount!r}"
        raise GatewayResponseError(msg)
    try:
        amount_minor = to_minor_units(amount=Decimal(str(amount)))
    except ValueError as exc:
        msg = f"tap charge amount is not a minor-unit amount: {amount!r}"
        raise GatewayResponseError(msg) from exc
    transaction = payload.get("transaction")
    redirect_url: str | None = None
    if isinstance(transaction, dict) and transaction.get("url"):
        redirect_url = _str(transaction, "url")
    return _Charge(
        id=_str(payload, "id"),
        status=_str(payload, "status"),
        amount_minor=amount_minor,
        currency=_str(payload, "currency"),
        redirect_url=redirect_url,
        saved_card=_extract_saved_card(payload),
        raw=payload,
    )


def _planted_reference(payload: dict[str, Any]) -> str:
    """The merchant reference we planted (``reference.transaction``) as Tap
    echoes it. NOT part of the hashstring - the service binds the signed
    charge id to the row instead of trusting this alone."""
    return _str(_dict(payload, "reference"), "transaction")


def _refund_result(payload: Any) -> RefundResult:
    if not isinstance(payload, dict):
        msg = f"tap refund response is not an object: {payload}"
        raise GatewayResponseError(msg)
    status = _str(payload, "status").upper()
    if status in _REFUND_DONE:
        outcome = RefundStatus.SUCCEEDED
    elif status in _REFUND_FAILED:
        outcome = RefundStatus.FAILED
    else:
        outcome = RefundStatus.PENDING
    return RefundResult(status=outcome, refund_id=_str(payload, "id"), raw=payload)


def _extract_saved_card(payload: dict[str, Any]) -> SavedCardData | None:
    """Stored-card payload from a charge object, or None.

    Tap echoes a ``card`` object on ordinary charges too - only a ``card_...``
    id means the card was actually vaulted (save_card was on and the account
    feature is enabled). The service layer adds the consent gate on top.
    """
    card = payload.get("card")
    if not isinstance(card, dict) or not str(card.get("id", "")).startswith("card_"):
        return None
    customer = _dict(payload, "customer")  # a vaulted card lives under one
    agreement = payload.get("payment_agreement")
    return SavedCardData(
        token=_str(card, "id"),
        customer_id=_str(customer, "id"),
        # Absent when the account's payment-agreement feature is off: the
        # card then serves CIT only (charge_saved refuses without it).
        agreement_id=_str(agreement, "id") if isinstance(agreement, dict) else "",
        brand=_str(card, "brand"),
        last4=_str(card, "last_four"),
        exp_month=_optional_int(card, "exp_month"),
        exp_year=_optional_int(card, "exp_year"),
        email=_str(customer, "email") if "email" in customer else "",
    )


def _str(obj: Any, key: str) -> str:
    value = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(value, str) or not value:
        msg = f"tap payload field {key!r} missing or not a string: {value!r}"
        raise GatewayResponseError(msg)
    return value


def _dict(obj: dict[str, Any], key: str) -> dict[str, Any]:
    value = obj.get(key)
    if not isinstance(value, dict):
        msg = f"tap payload field {key!r} missing or not an object: {value!r}"
        raise GatewayResponseError(msg)
    return value


def _optional_int(obj: dict[str, Any], key: str) -> int | None:
    if key not in obj:
        return None
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"tap payload field {key!r} is not an integer: {value!r}"
        raise GatewayResponseError(msg)
    return value


def _signed(obj: dict[str, Any], key: str) -> Any:
    """A field that takes part in the hashstring - its absence is a
    verification failure that names it."""
    try:
        return obj[key]
    except KeyError as exc:
        msg = f"webhook payload lacks signed field {key!r}"
        raise WebhookVerificationError(msg) from exc


def _format_amount(amount: object) -> str:
    """Tap hashes the amount formatted to the currency's decimal places. A
    non-numeric amount raises (``decimal.InvalidOperation``) - it cannot
    have been signed by Tap and must not be hashed as text."""
    return f"{Decimal(str(amount)):.{MINOR_UNITS}f}"
