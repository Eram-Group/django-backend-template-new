"""TapGateway: checkout payloads, hashstring verification, status, refund."""

import hashlib
import hmac
import json
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.common.http import OutboundStatusError
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.tap import TapGateway

SECRET = "sk_test_tap"  # noqa: S105 - test fixture value
CHARGES = "https://api.tap.company/v2/charges/"
TOKENS = "https://api.tap.company/v2/tokens"
CARD_REF = SavedCardRef(
    token="card_abc123",  # noqa: S106 - test fixture value
    customer_id="cus_xyz789",
    agreement_id="payment_agreement_555",
)


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


def _saved_request(*, agreement_id: str = "payment_agreement_555") -> CheckoutRequest:
    ref = SavedCardRef(
        token=CARD_REF.token,
        customer_id=CARD_REF.customer_id,
        agreement_id=agreement_id,
    )
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
        saved_card=ref,
    )


@respx.mock
def test_create_checkout_sends_save_card_flag() -> None:
    route = respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200, json={"id": "chg_1", "transaction": {"url": "https://pay.tap/x"}}
        )
    )
    request = CheckoutRequest(
        reference="ref-123",
        amount=Decimal("50.00"),
        currency="SAR",
        description="Top-up",
        customer_email="omar@example.com",
        customer_name="Omar",
        customer_phone="",
        webhook_url="https://backend.example.com/api/v1/payments/webhooks/tap",
        redirect_url="https://app.example.com/payments/x/return",
        save_card=True,
    )

    TapGateway().create_checkout(request=request)

    payload = json.loads(route.calls.last.request.content)
    assert payload["save_card"] is True
    assert payload["threeDSecure"] is True  # saving requires 3DS


@respx.mock
def test_create_checkout_with_saved_card_creates_token_then_charge() -> None:
    token_route = respx.post(TOKENS).mock(
        return_value=httpx.Response(200, json={"id": "tok_once"})
    )
    charge_route = respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "chg_2",
                "status": "INITIATED",
                "transaction": {"url": "https://pay.tap/3ds"},
            },
        )
    )

    session = TapGateway().create_checkout(request=_saved_request())

    token_body = json.loads(token_route.calls.last.request.content)
    assert token_body == {
        "saved_card": {"card_id": "card_abc123", "customer_id": "cus_xyz789"}
    }
    charge_body = json.loads(charge_route.calls.last.request.content)
    assert charge_body["source"] == {"id": "tok_once", "on_file": True}
    assert charge_body["customer"] == {"id": "cus_xyz789"}
    assert charge_body["payment_agreement"] == {"id": "payment_agreement_555"}
    assert charge_body["customer_initiated"] is True
    assert charge_body["threeDSecure"] is True
    # 3DS challenge: redirect, not a final outcome.
    assert session.checkout_url == "https://pay.tap/3ds"
    assert session.status == ""
    assert session.is_paid is False


@respx.mock
def test_create_checkout_with_saved_card_captured_without_redirect() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    respx.post(CHARGES).mock(
        return_value=httpx.Response(200, json={"id": "chg_2", "status": "CAPTURED"})
    )

    session = TapGateway().create_checkout(request=_saved_request())

    assert session.checkout_url == ""
    assert session.is_paid is True
    assert session.status == "CAPTURED"
    assert session.transaction_id == "chg_2"


@respx.mock
def test_charge_saved_is_mit_non_3ds() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    charge_route = respx.post(CHARGES).mock(
        return_value=httpx.Response(200, json={"id": "chg_3", "status": "CAPTURED"})
    )

    session = TapGateway().charge_saved(request=_saved_request())

    charge_body = json.loads(charge_route.calls.last.request.content)
    assert charge_body["customer_initiated"] is False
    assert charge_body["threeDSecure"] is False
    assert charge_body["payment_agreement"] == {"id": "payment_agreement_555"}
    # Crash recovery: the webhook URL rides along even for sync outcomes.
    assert charge_body["post"] == {
        "url": "https://backend.example.com/api/v1/payments/webhooks/tap"
    }
    assert session.is_paid is True


@respx.mock
def test_charge_saved_declined_maps_failed_result() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    respx.post(CHARGES).mock(
        return_value=httpx.Response(200, json={"id": "chg_4", "status": "DECLINED"})
    )

    session = TapGateway().charge_saved(request=_saved_request())

    assert session.checkout_url == ""
    assert session.is_paid is False
    assert session.status == "DECLINED"


@respx.mock
def test_charge_saved_without_agreement_is_loud() -> None:
    """Non-3DS is only legal with a payment agreement - refuse before HTTP."""
    token_route = respx.post(TOKENS).mock(
        return_value=httpx.Response(200, json={"id": "tok_once"})
    )

    with pytest.raises(GatewayResponseError):
        TapGateway().charge_saved(request=_saved_request(agreement_id=""))

    assert token_route.call_count == 0


def test_webhook_extracts_saved_card_payload() -> None:
    payload = _webhook_payload()
    payload["card"] = {"id": "card_abc123", "brand": "VISA", "last_four": "1019"}
    payload["customer"] = {"id": "cus_xyz789", "email": "omar@example.com"}
    payload["payment_agreement"] = {"id": "payment_agreement_555"}

    event = TapGateway().parse_webhook(
        headers={"hashstring": _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )

    assert event.saved_card is not None
    assert event.saved_card.token == "card_abc123"  # noqa: S105 - fixture value
    assert event.saved_card.customer_id == "cus_xyz789"
    assert event.saved_card.agreement_id == "payment_agreement_555"
    assert event.saved_card.brand == "VISA"
    assert event.saved_card.last4 == "1019"


def test_webhook_without_stored_card_has_no_saved_card_payload() -> None:
    """A transient card echo (no card_ id) must not look like a vaulted one."""
    payload = _webhook_payload()
    payload["card"] = {"id": "tok_transient", "brand": "VISA", "last_four": "1019"}

    event = TapGateway().parse_webhook(
        headers={"hashstring": _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )

    assert event.saved_card is None


@respx.mock
def test_delete_saved_card_calls_gateway() -> None:
    route = respx.delete("https://api.tap.company/v2/card/cus_xyz789/card_abc123").mock(
        return_value=httpx.Response(200, json={"deleted": True})
    )

    assert TapGateway().delete_saved_card(saved_card=CARD_REF) is True
    assert route.call_count == 1


# --- setup_card: add a card without payment (authorize + auto-void) ----------

AUTHORIZE = "https://api.tap.company/v2/authorize/"


@respx.mock
def test_setup_card_sends_hosted_authorize_with_auto_void() -> None:
    route = respx.post(AUTHORIZE).mock(
        return_value=httpx.Response(
            200, json={"id": "auth_1", "transaction": {"url": "https://pay.tap/a"}}
        )
    )

    session = TapGateway().setup_card(request=_request())

    body = json.loads(route.calls.last.request.content)
    assert body["save_card"] is True
    assert body["threeDSecure"] is True
    assert body["source"] == {"id": "src_card"}
    assert body["auto"] == {"type": "VOID", "time": 1}
    assert body["reference"]["transaction"] == "ref-123"
    assert body["post"]["url"].endswith("/webhooks/tap")
    assert body["customer"]["email"] == "omar@example.com"
    assert session.charge_id == "auth_1"
    assert session.checkout_url == "https://pay.tap/a"
    assert session.status == ""  # pending - the AUTHORIZED webhook settles it


@respx.mock
def test_setup_card_without_url_is_loud() -> None:
    respx.post(AUTHORIZE).mock(return_value=httpx.Response(200, json={"id": "auth_1"}))

    with pytest.raises(GatewayResponseError):
        TapGateway().setup_card(request=_request())


def test_webhook_authorized_settles_an_authorize() -> None:
    payload = _webhook_payload() | {"id": "auth_1", "status": "AUTHORIZED"}

    event = TapGateway().parse_webhook(
        headers={"hashstring": _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )

    assert event.is_paid is True
    assert event.transaction_id == "auth_1"


def test_webhook_authorized_never_settles_a_charge() -> None:
    payload = _webhook_payload() | {"status": "AUTHORIZED"}  # id stays chg_1

    event = TapGateway().parse_webhook(
        headers={"hashstring": _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )

    assert event.is_paid is False


def test_webhook_void_does_not_settle_an_authorize() -> None:
    payload = _webhook_payload() | {"id": "auth_1", "status": "VOID"}

    event = TapGateway().parse_webhook(
        headers={"hashstring": _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )

    assert event.is_paid is False


@respx.mock
def test_fetch_status_routes_auth_ids_to_authorize() -> None:
    route = respx.get("https://api.tap.company/v2/authorize/auth_1").mock(
        return_value=httpx.Response(200, json={"id": "auth_1", "status": "AUTHORIZED"})
    )

    status = TapGateway().fetch_status(charge_id="auth_1", reference="ref-123")

    assert route.called
    assert status.is_paid is True


@respx.mock
def test_setup_card_with_sdk_token_uses_token_source() -> None:
    route = respx.post(AUTHORIZE).mock(
        return_value=httpx.Response(
            200, json={"id": "auth_2", "transaction": {"url": "https://3ds.tap/x"}}
        )
    )

    session = TapGateway().setup_card(
        request=replace(_request(), card_token="tok_sdk_1")  # noqa: S106 - fixture
    )

    body = json.loads(route.calls.last.request.content)
    assert body["source"] == {"id": "tok_sdk_1"}  # our form collected the card
    assert session.checkout_url == "https://3ds.tap/x"  # only the 3DS challenge


@respx.mock
def test_setup_card_with_sdk_token_frictionless_settles() -> None:
    respx.post(AUTHORIZE).mock(
        return_value=httpx.Response(200, json={"id": "auth_2", "status": "AUTHORIZED"})
    )

    session = TapGateway().setup_card(
        request=replace(_request(), card_token="tok_sdk_1")  # noqa: S106 - fixture
    )

    assert session.checkout_url == ""
    assert session.is_paid is True
    assert session.status == "AUTHORIZED"
