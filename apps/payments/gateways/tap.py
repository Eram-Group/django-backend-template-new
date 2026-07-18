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
from apps.payments.gateways.base import SavedCardData
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.base import WebhookVerificationError

_BASE = "https://api.tap.company/v2"
_PAID_STATUS = "CAPTURED"
# Statuses that mean "not settled yet" on a charge response WITHOUT a
# redirect URL - the row stays PENDING for payment_verify / the reconcile
# sweep rather than being declared final here.
_PENDING_STATUSES = {"INITIATED", "IN_PROGRESS"}


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
        if request.saved_card is not None:  # one-click CIT with a stored card
            return self._charge_saved_card(request=request, customer_initiated=True)
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
                "threeDSecure": True,  # saving requires 3DS; harmless otherwise
                "save_card": request.save_card,
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
            headers=_headers(),
            json={"saved_card": {"card_id": ref.token, "customer_id": ref.customer_id}},
            # Token creation moves no money; a duplicate single-use token just
            # expires unused in 5 minutes.
            retry="transient",
        )
        token_id = token_response.json().get("id")
        if not token_id:
            msg = f"tap token response missing id: {token_response.json()}"
            raise GatewayResponseError(msg)
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
            "source": {"id": str(token_id), "on_file": True},
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
            headers=_headers(),
            json=body,
            retry="connect-only",
        )
        payload = response.json()
        charge_id = payload.get("id")
        if not charge_id:
            msg = f"tap charge response missing id: {payload}"
            raise GatewayResponseError(msg)
        status = str(payload.get("status", ""))
        checkout_url = str(payload.get("transaction", {}).get("url") or "")
        if checkout_url:  # 3DS challenge - the redirect + webhook settle it
            return CheckoutSession(
                charge_id=str(charge_id), checkout_url=checkout_url, raw=payload
            )
        final = status.upper() not in _PENDING_STATUSES
        return CheckoutSession(
            charge_id=str(charge_id),
            checkout_url="",
            raw=payload,
            is_paid=final and status.upper() == _PAID_STATUS,
            status=status if final else "",
            transaction_id=str(charge_id) if final else "",
        )

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> bool:
        response = request_json(
            service="tap",
            method="DELETE",
            url=f"{_BASE}/card/{saved_card.customer_id}/{saved_card.token}",
            headers=_headers(),
            retry="connect-only",
        )
        return response.json().get("deleted") is not False

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
            saved_card=_extract_saved_card(payload),
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
            saved_card=_extract_saved_card(payload),
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


def _extract_saved_card(payload: dict[str, Any]) -> SavedCardData | None:
    """Stored-card payload from a charge response/webhook, or None.

    Tap echoes a ``card`` object on ordinary charges too - only a ``card_...``
    id means the card was actually vaulted (save_card was on and the account
    feature is enabled). The service layer adds the consent gate on top.
    """
    card = payload.get("card") or {}
    token = str(card.get("id") or "")
    if not token.startswith("card_"):
        return None
    customer = payload.get("customer") or {}
    agreement = payload.get("payment_agreement") or {}
    return SavedCardData(
        token=token,
        customer_id=str(customer.get("id") or ""),
        agreement_id=str(agreement.get("id") or ""),
        brand=str(card.get("brand") or ""),
        last4=str(card.get("last_four") or ""),
        exp_month=int(card["exp_month"]) if card.get("exp_month") else None,
        exp_year=int(card["exp_year"]) if card.get("exp_year") else None,
        email=str(customer.get("email") or ""),
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
