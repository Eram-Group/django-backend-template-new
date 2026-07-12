"""TapGateway: checkout payloads, hashstring verification, status, refund."""

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.common.http import OutboundStatusError
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.tap import TapGateway

SECRET = "sk_test_tap"  # noqa: S105 - test fixture value
CHARGES = "https://api.tap.company/v2/charges/"


@pytest.fixture(autouse=True)
def _tap_creds(settings: Any) -> None:
    settings.TAP_SECRET_KEY = SecretStr(SECRET)


def _request() -> CheckoutRequest:
    return CheckoutRequest(
        reference="ref-123",
        amount=Decimal("50.00"),
        currency="SAR",
        description="Top-up",
        customer_email="omar@example.com",
        customer_name="Omar",
        customer_phone="+966501234567",
        webhook_url="https://backend.example.com/api/v1/payments/webhooks/tap",
        redirect_url="https://app.example.com/payments/x/return",
    )


@respx.mock
def test_create_checkout_plants_reference_and_customer() -> None:
    route = respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200, json={"id": "chg_1", "transaction": {"url": "https://pay.tap/x"}}
        )
    )

    session = TapGateway().create_checkout(request=_request())

    assert session.charge_id == "chg_1"
    assert session.checkout_url == "https://pay.tap/x"
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    payload = json.loads(request.content)
    assert payload["reference"]["transaction"] == "ref-123"
    assert payload["amount"] == "50.00"
    assert payload["customer"]["email"] == "omar@example.com"
    assert payload["customer"]["phone"]["country_code"] == 966


@respx.mock
def test_create_checkout_5xx_is_not_retried() -> None:
    """POST uses connect-only retry - a 500 may have created the charge."""
    route = respx.post(CHARGES).mock(return_value=httpx.Response(500))

    with pytest.raises(OutboundStatusError):
        TapGateway().create_checkout(request=_request())

    assert route.call_count == 1


@respx.mock
def test_create_checkout_2xx_without_url_fails() -> None:
    respx.post(CHARGES).mock(return_value=httpx.Response(200, json={"id": "chg_1"}))

    with pytest.raises(GatewayResponseError):
        TapGateway().create_checkout(request=_request())


def _webhook_payload() -> dict[str, Any]:
    return {
        "id": "chg_1",
        "amount": 50.0,
        "currency": "SAR",
        "status": "CAPTURED",
        "reference": {
            "transaction": "ref-123",
            "payment": "pay_9",
            "gateway": "gw_7",
        },
        "transaction": {"created": "1760000000000"},
    }


def _sign(payload: dict[str, Any]) -> str:
    concatenated = (
        f"x_id{payload['id']}"
        f"x_amount{Decimal(str(payload['amount'])):.2f}"
        f"x_currency{payload['currency']}"
        f"x_gateway_reference{payload['reference']['gateway']}"
        f"x_payment_reference{payload['reference']['payment']}"
        f"x_status{payload['status']}"
        f"x_created{payload['transaction']['created']}"
    )
    return hmac.new(SECRET.encode(), concatenated.encode(), hashlib.sha256).hexdigest()


def test_webhook_with_valid_hashstring_parses() -> None:
    payload = _webhook_payload()

    event = TapGateway().parse_webhook(
        headers={"hashstring": _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )

    assert event.reference == "ref-123"
    assert event.transaction_id == "chg_1"
    assert event.is_paid is True


def test_webhook_with_tampered_amount_is_rejected() -> None:
    payload = _webhook_payload()
    signature = _sign(payload)
    payload["amount"] = 1.0  # attacker rewrites after signing

    with pytest.raises(WebhookVerificationError):
        TapGateway().parse_webhook(
            headers={"hashstring": signature},
            params={},
            body=json.dumps(payload).encode(),
        )


def test_webhook_without_hashstring_is_rejected() -> None:
    with pytest.raises(WebhookVerificationError):
        TapGateway().parse_webhook(
            headers={}, params={}, body=json.dumps(_webhook_payload()).encode()
        )


@respx.mock
def test_fetch_status_maps_captured() -> None:
    respx.get(f"{CHARGES.rstrip('/')}/chg_1").mock(
        return_value=httpx.Response(200, json={"id": "chg_1", "status": "CAPTURED"})
    )

    status = TapGateway().fetch_status(charge_id="chg_1", reference="ref-123")

    assert status.is_paid is True
    assert status.transaction_id == "chg_1"


@respx.mock
def test_refund_maps_status() -> None:
    respx.post("https://api.tap.company/v2/refunds/").mock(
        return_value=httpx.Response(200, json={"id": "re_1", "status": "PENDING"})
    )

    result = TapGateway().refund(
        transaction_id="chg_1", amount=Decimal("50.00"), currency="SAR"
    )

    assert result.ok is True
