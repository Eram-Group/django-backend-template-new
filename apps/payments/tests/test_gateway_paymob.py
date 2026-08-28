"""PaymobGateway: intention payloads, HMAC-SHA512 verification, inquiry, refund."""

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from django.core.cache import cache
from pydantic import SecretStr

from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEventKind
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.paymob import PaymobGateway

SECRET = "skey_test_paymob"
HMAC_SECRET = "hmac_test_secret"
API_KEY = "api_test_paymob"
INTENTION = "https://accept.paymob.com/v1/intention/"
PAY = "https://accept.paymob.com/api/acceptance/payments/pay"
AUTH_TOKENS = "https://accept.paymob.com/api/auth/tokens"
INQUIRY = "https://accept.paymob.com/api/ecommerce/orders/transaction_inquiry"
CARD_TOKEN = "tok_saved_paymob_1"  # gitleaks:allow - fake fixture token


@pytest.fixture(autouse=True)
def _paymob_creds(settings: Any) -> None:
    settings.PAYMOB_SECRET_KEY = SecretStr(SECRET)
    settings.PAYMOB_PUBLIC_KEY = "pk_test_paymob"
    settings.PAYMOB_HMAC_SECRET = SecretStr(HMAC_SECRET)
    settings.PAYMOB_API_KEY = SecretStr(API_KEY)
    settings.PAYMOB_INTEGRATION_IDS = [11, 22]
    cache.clear()  # the auth-token cache must not leak between tests


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
    assert payload["expiration"] == 3600  # documented maximum, sent explicitly
    assert payload["billing_data"]["first_name"] == "Omar"
    assert payload["billing_data"]["last_name"] == "-"  # required, no surname
    # Unified Checkout lives on the per-region host since July 2026.
    assert session.checkout_url == (
        "https://eg.checkout.paymob.com/?publicKey=pk_test_paymob&clientSecret=cs_abc"
    )


@respx.mock
def test_create_checkout_splits_full_name_into_billing_fields() -> None:
    route = respx.post(INTENTION).mock(
        return_value=httpx.Response(200, json={"id": "int_1", "client_secret": "cs"})
    )
    request = CheckoutRequest(
        reference="ref-456",
        amount=Decimal("10.00"),
        currency="EGP",
        description="",
        customer_email="omar@example.com",
        customer_name="Omar Ahmed Gawdat",
        customer_phone="",
        webhook_url="https://backend.example.com/hook",
        redirect_url="https://app.example.com/return",
    )

    PaymobGateway().create_checkout(request=request)

    billing = json.loads(route.calls.last.request.content)["billing_data"]
    assert billing["first_name"] == "Omar"
    assert billing["last_name"] == "Ahmed Gawdat"
    assert billing["phone_number"] == "+20000000000"  # Paymob rejects a blank


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
    assert event.is_pending is False
    assert event.status == "success"
    # HMAC-signed amount/currency ride along for the service's cross-check.
    assert event.amount_minor == 7550
    assert event.currency == "EGP"


def _parse(obj: dict[str, Any]) -> Any:
    return PaymobGateway().parse_webhook(
        headers={},
        params={"hmac": _sign(obj)},
        body=json.dumps({"type": "TRANSACTION", "obj": obj}).encode(),
    )


def test_webhook_declined_is_failed_not_pending() -> None:
    obj = _webhook_obj()
    obj["success"] = False

    event = _parse(obj)

    assert event.is_paid is False
    assert event.is_pending is False
    assert event.status == "failed"


def test_webhook_pending_is_informational() -> None:
    """success=false + pending=true is the customer on the bank's OTP page
    (or a kiosk reference awaiting cash) - not a decline."""
    obj = _webhook_obj()
    obj["success"] = False
    obj["pending"] = True

    event = _parse(obj)

    assert event.is_paid is False
    assert event.is_pending is True
    assert event.status == "pending"


def test_webhook_refund_child_never_transitions_or_retargets() -> None:
    """A refund/void/capture arrives as a CHILD transaction on the same
    order: its own id must not replace the settled transaction id, and its
    amount is the refund's, not the payment's."""
    obj = _webhook_obj()
    obj.update({"has_parent_transaction": True, "id": 111222, "is_refund": True})
    obj["amount_cents"] = 2000  # partial refund

    event = _parse(obj)

    assert event.reference == "ref-456"  # still finds our row (audit)
    assert event.is_paid is False
    assert event.is_pending is True
    assert event.status == "refund"
    assert event.transaction_id == ""
    assert event.amount_minor is None
    assert event.currency == ""


def test_webhook_void_child_is_reported_as_void() -> None:
    obj = _webhook_obj()
    obj.update({"has_parent_transaction": True, "is_void": True})

    assert _parse(obj).status == "void"


def test_webhook_authorization_without_capture_is_not_paid() -> None:
    """An Auth integration only holds funds; we never capture, so it must
    not credit anything."""
    obj = _webhook_obj()
    obj["is_auth"] = True
    obj["is_standalone_payment"] = False

    event = _parse(obj)

    assert event.is_paid is False
    assert event.is_pending is True
    assert event.status == "authorized"


@pytest.mark.parametrize(
    ("flag", "status"), [("is_refunded", "refunded"), ("is_voided", "voided")]
)
def test_webhook_reversed_parent_is_informational(flag: str, status: str) -> None:
    obj = _webhook_obj()
    obj[flag] = True

    event = _parse(obj)

    assert event.is_paid is False
    assert event.is_pending is True
    assert event.status == status


def test_webhook_without_merchant_order_id_has_an_empty_reference() -> None:
    """Paymob's own callback sample carries ``merchant_order_id: null``."""
    obj = _webhook_obj()
    obj["order"] = {"id": 555, "merchant_order_id": None}

    event = _parse(obj)

    assert event.reference == ""


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


@pytest.mark.parametrize("body", [b"[]", b"null", b'"text"', b"42"])
def test_webhook_with_non_object_body_is_rejected(body: bytes) -> None:
    """Valid JSON that is not an object must be a clean rejection, not an
    AttributeError 500 that makes the gateway retry a body it can never
    verify."""
    with pytest.raises(WebhookVerificationError):
        PaymobGateway().parse_webhook(
            headers={}, params={"hmac": _sign(_webhook_obj())}, body=body
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
def test_fetch_status_by_merchant_order_id_with_cached_auth_token() -> None:
    """The inquiry API still authenticates with a one-hour auth token IN THE
    BODY (minted from the dashboard API key), not the secret-key header the
    intention API takes - and the token is cached across calls."""
    auth_route = respx.post(AUTH_TOKENS).mock(
        return_value=httpx.Response(201, json={"token": "auth_1"})
    )
    route = respx.post(INQUIRY).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 987654,
                "success": True,
                "pending": False,
                "is_refunded": False,
                "amount_cents": 7550,
                "currency": "EGP",
            },
        )
    )

    status = PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")
    PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")

    assert status.is_paid is True
    assert status.transaction_id == "987654"
    assert status.amount_minor == 7550
    assert status.currency == "EGP"
    assert json.loads(auth_route.calls.last.request.content) == {"api_key": API_KEY}
    inquiry = route.calls.last.request
    assert "Authorization" not in inquiry.headers
    assert json.loads(inquiry.content) == {
        "auth_token": "auth_1",
        "merchant_order_id": "ref-456",
    }
    assert auth_route.call_count == 1  # second inquiry rode the cached token
    assert route.call_count == 2


@respx.mock
def test_fetch_status_without_transaction_is_pending_not_an_outage() -> None:
    """An order nobody paid yet has no transaction - Paymob answers 404. That
    is "still pending" for the polling client, not a 503."""
    respx.post(AUTH_TOKENS).mock(
        return_value=httpx.Response(201, json={"token": "auth_1"})
    )
    respx.post(INQUIRY).mock(
        return_value=httpx.Response(404, json={"detail": "Transaction not found"})
    )

    status = PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")

    assert status.is_paid is False
    assert status.is_pending is True
    assert status.status == "no_transaction"
    assert status.transaction_id == ""


@respx.mock
def test_fetch_status_refund_child_is_not_paid() -> None:
    """The inquiry returns the LAST transaction on the order - after a
    refund that is the child, which must not read as a fresh payment."""
    respx.post(AUTH_TOKENS).mock(
        return_value=httpx.Response(201, json={"token": "auth_1"})
    )
    respx.post(INQUIRY).mock(
        return_value=httpx.Response(
            200,
            json={"id": 111, "success": True, "has_parent_transaction": True},
        )
    )

    status = PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")

    assert status.is_paid is False
    assert status.is_pending is True


@respx.mock
def test_fetch_status_without_api_key_is_loud(settings: Any) -> None:
    settings.PAYMOB_API_KEY = None
    route = respx.post(INQUIRY).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GatewayResponseError):
        PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")

    assert route.call_count == 0


@respx.mock
def test_fetch_status_with_tokenless_auth_response_is_loud() -> None:
    respx.post(AUTH_TOKENS).mock(return_value=httpx.Response(201, json={}))

    with pytest.raises(GatewayResponseError):
        PaymobGateway().fetch_status(charge_id="int_1", reference="ref-456")


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
    assert event.saved_card.exp_month is None  # older payloads carry no expiry
    assert event.saved_card.exp_year is None


def test_token_webhook_parses_expiry_when_present() -> None:
    """The Aug-2026 token payload adds expiry_month/expiry_year ("01"/"38");
    they sit outside the HMAC, so they are display data only."""
    obj = _token_obj()
    obj.update({"expiry_month": "01", "expiry_year": "38", "cardholder_name": "T"})

    event = PaymobGateway().parse_webhook(
        headers={},
        params={"hmac": _sign_token(obj)},
        body=json.dumps({"type": "TOKEN", "obj": obj}).encode(),
    )

    assert event.saved_card is not None
    assert event.saved_card.exp_month == 1
    assert event.saved_card.exp_year == 2038


@pytest.mark.parametrize("masked", ["xxxx-xxxx-xxxx-23", "", "xxxx"])
def test_token_webhook_with_short_masked_pan_has_no_last4(masked: str) -> None:
    obj = _token_obj()
    obj["masked_pan"] = masked

    event = PaymobGateway().parse_webhook(
        headers={},
        params={"hmac": _sign_token(obj)},
        body=json.dumps({"type": "TOKEN", "obj": obj}).encode(),
    )

    assert event.saved_card is not None
    assert event.saved_card.last4 == ""


def test_token_webhook_with_tampered_token_is_rejected() -> None:
    obj = _token_obj()
    signature = _sign_token(obj)
    obj["token"] = "tok_attacker"

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


@respx.mock
def test_charge_saved_pending_pay_leaves_the_row_to_the_webhook(
    settings: Any,
) -> None:
    """A MOTO pay answered ``pending`` is not an outcome yet: an empty
    session status keeps the Payment PENDING for the callback/sweep."""
    settings.PAYMOB_MOTO_INTEGRATION_ID = 44
    respx.post(INTENTION).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "int_9",
                "client_secret": "cs_moto",
                "payment_keys": [{"integration": 44, "key": "pk_moto_1"}],
            },
        )
    )
    respx.post(PAY).mock(
        return_value=httpx.Response(
            200, json={"id": 123321, "success": False, "pending": True}
        )
    )

    session = PaymobGateway().charge_saved(request=_saved_request())

    assert session.is_paid is False
    assert session.status == ""
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
