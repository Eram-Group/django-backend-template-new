"""PaymobGateway: intention payloads, HMAC-SHA512 verification, inquiry, refund."""

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEventKind
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.paymob import PaymobGateway

SECRET = "skey_test_paymob"  # noqa: S105 - test fixture value
HMAC_SECRET = "hmac_test_secret"  # noqa: S105 - test fixture value
INTENTION = "https://accept.paymob.com/v1/intention/"
PAY = "https://accept.paymob.com/api/acceptance/payments/pay"
CARD_TOKEN = "tok_saved_paymob_1"  # noqa: S105 - test fixture; gitleaks:allow


@pytest.fixture(autouse=True)
def _paymob_creds(settings: Any) -> None:
    settings.PAYMOB_SECRET_KEY = SecretStr(SECRET)
    settings.PAYMOB_PUBLIC_KEY = "pk_test_paymob"
    settings.PAYMOB_HMAC_SECRET = SecretStr(HMAC_SECRET)
    settings.PAYMOB_INTEGRATION_IDS = [11, 22]


def _request() -> CheckoutRequest:
    return CheckoutRequest(
        reference="ref-456",
        amount=Decimal("75.50"),
        currency="EGP",
        description="Top-up",
        customer_email="omar@example.com",
        customer_name="Omar",
        customer_phone="+201001234567",
        webhook_url="https://backend.example.com/api/v1/payments/webhooks/paymob",
        redirect_url="https://app.example.com/payments/x/return",
    )


@respx.mock
def test_create_checkout_uses_minor_units_and_special_reference() -> None:
    route = respx.post(INTENTION).mock(
        return_value=httpx.Response(
            200, json={"id": "int_1", "client_secret": "cs_abc"}
        )
    )

    session = PaymobGateway().create_checkout(request=_request())

    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Token {SECRET}"
    payload = json.loads(request.content)
    assert payload["amount"] == 7550  # integer minor units, never float math
    assert payload["special_reference"] == "ref-456"  # idempotent at Paymob
    assert payload["payment_methods"] == [11, 22]
    assert "publicKey=pk_test_paymob" in session.checkout_url
    assert "clientSecret=cs_abc" in session.checkout_url


def test_create_checkout_without_integration_ids_is_loud(settings: Any) -> None:
    settings.PAYMOB_INTEGRATION_IDS = []

    with pytest.raises(GatewayResponseError):
        PaymobGateway().create_checkout(request=_request())


def _webhook_obj() -> dict[str, Any]:
    return {
        "amount_cents": 7550,
        "created_at": "2026-07-12T10:00:00",
        "currency": "EGP",
        "error_occured": False,
        "has_parent_transaction": False,
        "id": 987654,
        "integration_id": 11,
        "is_3d_secure": True,
        "is_auth": False,
        "is_capture": False,
        "is_refunded": False,
        "is_standalone_payment": True,
        "is_voided": False,
        "order": {"id": 555, "merchant_order_id": "ref-456"},
        "owner": 42,
        "pending": False,
        "source_data": {"pan": "1234", "sub_type": "MasterCard", "type": "card"},
        "success": True,
    }


def _sign(obj: dict[str, Any], key: str = HMAC_SECRET) -> str:
    concatenated = (
        f"{obj['amount_cents']}{obj['created_at']}{obj['currency']}"
        f"{'true' if obj['error_occured'] else 'false'}"
        f"{'true' if obj['has_parent_transaction'] else 'false'}"
        f"{obj['id']}{obj['integration_id']}"
        f"{'true' if obj['is_3d_secure'] else 'false'}"
        f"{'true' if obj['is_auth'] else 'false'}"
        f"{'true' if obj['is_capture'] else 'false'}"
        f"{'true' if obj['is_refunded'] else 'false'}"
        f"{'true' if obj['is_standalone_payment'] else 'false'}"
        f"{'true' if obj['is_voided'] else 'false'}"
        f"{obj['order']['id']}{obj['owner']}"
        f"{'true' if obj['pending'] else 'false'}"
        f"{obj['source_data']['pan']}{obj['source_data']['sub_type']}"
        f"{obj['source_data']['type']}"
        f"{'true' if obj['success'] else 'false'}"
    )
    return hmac.new(key.encode(), concatenated.encode(), hashlib.sha512).hexdigest()


def test_webhook_with_valid_hmac_parses() -> None:
    obj = _webhook_obj()

    event = PaymobGateway().parse_webhook(
        headers={},
        params={"hmac": _sign(obj)},
        body=json.dumps({"type": "TRANSACTION", "obj": obj}).encode(),
    )

    assert event.reference == "ref-456"
    assert event.transaction_id == "987654"
    assert event.is_paid is True


def test_webhook_with_tampered_success_flag_is_rejected() -> None:
    obj = _webhook_obj()
    signature = _sign(obj)
    obj["success"] = False
    obj["is_refunded"] = True

    with pytest.raises(WebhookVerificationError):
        PaymobGateway().parse_webhook(
            headers={},
            params={"hmac": signature},
            body=json.dumps({"obj": obj}).encode(),
        )


def test_webhook_without_hmac_param_is_rejected() -> None:
    with pytest.raises(WebhookVerificationError):
        PaymobGateway().parse_webhook(
            headers={}, params={}, body=json.dumps({"obj": _webhook_obj()}).encode()
        )


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_webhook_with_blank_secret_fails_closed(settings: Any, blank: str) -> None:
    """A blank signing secret must refuse the webhook, never sign with it.

    env.py normalises a blank secret to None before it reaches settings, but
    verification must not depend on that: a blank key yields a digest the
    caller can compute too, so the check would wave through a forged payload.
    """
    settings.PAYMOB_HMAC_SECRET = SecretStr(blank)
    obj = _webhook_obj()

    with pytest.raises(WebhookVerificationError):
        PaymobGateway().parse_webhook(
            headers={},
            # The signature an attacker would send, knowing the key is blank.
            params={"hmac": _sign(obj, key=blank)},
            body=json.dumps({"obj": obj}).encode(),
        )


@respx.mock
def test_fetch_status_by_merchant_order_id() -> None:
    """The old template raised NotImplementedError here."""
    route = respx.post(
        "https://accept.paymob.com/api/ecommerce/orders/transaction_inquiry"
    ).mock(
        return_value=httpx.Response(
            200, json={"id": 987654, "success": True, "is_refunded": False}
        )
    )

    status = PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")

    assert status.is_paid is True
    assert json.loads(route.calls.last.request.content) == {
        "merchant_order_id": "ref-456"
    }


@respx.mock
def test_refund_sends_minor_units() -> None:
    route = respx.post(
        "https://accept.paymob.com/api/acceptance/void_refund/refund"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    result = PaymobGateway().refund(
        transaction_id="987654", amount=Decimal("75.50"), currency="EGP"
    )

    assert result.ok is True
    assert json.loads(route.calls.last.request.content)["amount_cents"] == 7550


def _saved_request() -> CheckoutRequest:
    return CheckoutRequest(
        reference="ref-456",
        amount=Decimal("75.50"),
        currency="EGP",
        description="Top-up",
        customer_email="omar@example.com",
        customer_name="Omar",
        customer_phone="+201001234567",
        webhook_url="https://backend.example.com/api/v1/payments/webhooks/paymob",
        redirect_url="https://app.example.com/payments/x/return",
        saved_card=SavedCardRef(token=CARD_TOKEN, customer_id="", agreement_id=""),
    )


def _token_obj() -> dict[str, Any]:
    return {
        "id": 12929738,
        "token": CARD_TOKEN,
        "masked_pan": "xxxx-xxxx-xxxx-2346",
        "merchant_id": 1058607,
        "card_subtype": "MasterCard",
        "created_at": "2026-07-18T14:16:05.038717",
        "email": "omar@example.com",
        "order_id": "454732315",
        "user_added": False,
    }


def _sign_token(obj: dict[str, Any], key: str = HMAC_SECRET) -> str:
    concatenated = (
        f"{obj['card_subtype']}{obj['created_at']}{obj['email']}{obj['id']}"
        f"{obj['masked_pan']}{obj['merchant_id']}{obj['order_id']}{obj['token']}"
    )
    return hmac.new(key.encode(), concatenated.encode(), hashlib.sha512).hexdigest()


def test_token_webhook_with_valid_hmac_parses() -> None:
    obj = _token_obj()

    event = PaymobGateway().parse_webhook(
        headers={},
        params={"hmac": _sign_token(obj)},
        body=json.dumps({"type": "TOKEN", "obj": obj}).encode(),
    )

    assert event.kind == WebhookEventKind.CARD_TOKEN
    assert event.reference == ""
    assert event.saved_card is not None
    assert event.saved_card.token == CARD_TOKEN
    assert event.saved_card.brand == "MasterCard"
    assert event.saved_card.last4 == "2346"  # digits out of the masked pan
    assert event.saved_card.email == "omar@example.com"


def test_token_webhook_with_tampered_token_is_rejected() -> None:
    obj = _token_obj()
    signature = _sign_token(obj)
    obj["token"] = "tok_attacker"  # noqa: S105 - test fixture value

    with pytest.raises(WebhookVerificationError):
        PaymobGateway().parse_webhook(
            headers={},
            params={"hmac": signature},
            body=json.dumps({"type": "TOKEN", "obj": obj}).encode(),
        )


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_token_webhook_with_blank_secret_fails_closed(
    settings: Any, blank: str
) -> None:
    settings.PAYMOB_HMAC_SECRET = SecretStr(blank)
    obj = _token_obj()

    with pytest.raises(WebhookVerificationError):
        PaymobGateway().parse_webhook(
            headers={},
            params={"hmac": _sign_token(obj, key=blank)},
            body=json.dumps({"type": "TOKEN", "obj": obj}).encode(),
        )


@respx.mock
def test_create_checkout_with_saved_card_uses_cof_integration_and_card_tokens(
    settings: Any,
) -> None:
    settings.PAYMOB_COF_INTEGRATION_ID = 33
    route = respx.post(INTENTION).mock(
        return_value=httpx.Response(200, json={"id": "int_2", "client_secret": "cs_x"})
    )

    PaymobGateway().create_checkout(request=_saved_request())

    payload = json.loads(route.calls.last.request.content)
    assert payload["payment_methods"] == [33]
    assert payload["card_tokens"] == [CARD_TOKEN]


@respx.mock
def test_create_checkout_with_saved_card_falls_back_to_default_integrations(
    settings: Any,
) -> None:
    """Paymob test mode has no Card-on-File id - the 3DS one accepts tokens."""
    settings.PAYMOB_COF_INTEGRATION_ID = None
    route = respx.post(INTENTION).mock(
        return_value=httpx.Response(200, json={"id": "int_2", "client_secret": "cs_x"})
    )

    PaymobGateway().create_checkout(request=_saved_request())

    payload = json.loads(route.calls.last.request.content)
    assert payload["payment_methods"] == [11, 22]
    assert payload["card_tokens"] == [CARD_TOKEN]


@respx.mock
def test_charge_saved_moto_intention_then_pay(settings: Any) -> None:
    settings.PAYMOB_MOTO_INTEGRATION_ID = 44
    intention_route = respx.post(INTENTION).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "int_9",
                "client_secret": "cs_moto",
                "payment_keys": [{"integration": 44, "key": "pk_moto_1"}],
            },
        )
    )
    pay_route = respx.post(PAY).mock(
        return_value=httpx.Response(200, json={"id": 123321, "success": True})
    )

    session = PaymobGateway().charge_saved(request=_saved_request())

    intention_body = json.loads(intention_route.calls.last.request.content)
    assert intention_body["payment_methods"] == [44]
    assert "card_tokens" not in intention_body
    assert intention_body["special_reference"] == "ref-456"  # webhook linkage
    pay_body = json.loads(pay_route.calls.last.request.content)
    assert pay_body == {
        "source": {"identifier": CARD_TOKEN, "subtype": "TOKEN"},
        "payment_token": "pk_moto_1",
    }
    assert session.checkout_url == ""
    assert session.is_paid is True
    assert session.status == "success"
    assert session.transaction_id == "123321"


def test_charge_saved_without_moto_integration_is_loud(settings: Any) -> None:
    settings.PAYMOB_MOTO_INTEGRATION_ID = None

    with pytest.raises(GatewayResponseError):
        PaymobGateway().charge_saved(request=_saved_request())


@respx.mock
def test_charge_saved_without_payment_keys_is_loud(settings: Any) -> None:
    settings.PAYMOB_MOTO_INTEGRATION_ID = 44
    respx.post(INTENTION).mock(
        return_value=httpx.Response(200, json={"id": "int_9", "client_secret": "cs"})
    )
    pay_route = respx.post(PAY).mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    with pytest.raises(GatewayResponseError):
        PaymobGateway().charge_saved(request=_saved_request())

    assert pay_route.call_count == 0


# --- setup_card: add a card without payment (Verification integration) -------


@respx.mock
def test_setup_card_uses_verification_integration(settings: Any) -> None:
    settings.PAYMOB_VERIFICATION_INTEGRATION_ID = 55
    route = respx.post(INTENTION).mock(
        return_value=httpx.Response(
            200, json={"id": "int_v", "client_secret": "cs_verify"}
        )
    )

    session = PaymobGateway().setup_card(request=_request())

    payload = json.loads(route.calls.last.request.content)
    assert payload["payment_methods"] == [55]
    assert payload["amount"] == 7550
    assert payload["special_reference"] == "ref-456"
    assert "clientSecret=cs_verify" in session.checkout_url


def test_setup_card_without_verification_integration_is_loud(settings: Any) -> None:
    settings.PAYMOB_VERIFICATION_INTEGRATION_ID = None

    with pytest.raises(GatewayResponseError):
        PaymobGateway().setup_card(request=_request())
