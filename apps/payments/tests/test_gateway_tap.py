"""TapGateway: checkout payloads, hashstring verification, status, refund."""

import hashlib
import hmac
import json
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.common.http import OutboundStatusError
from apps.payments.gateways.base import CardVaultGateway
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import GatewayConfigurationError
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import RefundStatus
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.tap import TapGateway

SECRET = "sk_test_tap"
CHARGES = "https://api.tap.company/v2/charges/"
TOKENS = "https://api.tap.company/v2/tokens"
CARD_URL = "https://api.tap.company/v2/card/cus_xyz789/card_abc123"
CARD_REF = SavedCardRef(
    token="card_abc123",
    customer_id="cus_xyz789",
    agreement_id="payment_agreement_555",
)


@pytest.fixture(autouse=True)
def _tap_creds(settings: Any) -> None:
    settings.TAP_SECRET_KEY = SecretStr(SECRET)


def _request(**overrides: Any) -> CheckoutRequest:
    fields: dict[str, Any] = {
        "reference": "ref-123",
        "amount": Decimal("50.00"),
        "currency": "SAR",
        "description": "Top-up",
        "customer_email": "omar@example.com",
        "customer_name": "Omar Gawdat",
        "customer_phone": "+966501234567",
        "webhook_url": "https://backend.example.com/api/v1/payments/webhooks/tap",
        "redirect_url": "https://app.example.com/payments/x/return",
        "saved_card": None,
        "customer_id": "",
    }
    fields.update(overrides)
    return CheckoutRequest(**fields)


def _charge(**overrides: Any) -> dict[str, Any]:
    """A Tap charge object as the API answers it (create/retrieve/webhook)."""
    payload: dict[str, Any] = {
        "id": "chg_1",
        "amount": 50.0,
        "currency": "SAR",
        "status": "INITIATED",
        "reference": {"transaction": "ref-123", "payment": "pay_9", "gateway": "gw_7"},
        "transaction": {"created": "1760000000000", "url": "https://pay.tap/x"},
    }
    payload.update(overrides)
    return payload


def test_constructor_requires_the_secret_key(settings: Any) -> None:
    settings.TAP_SECRET_KEY = None

    with pytest.raises(GatewayConfigurationError):
        TapGateway()


def test_tap_has_a_card_vault() -> None:
    assert isinstance(TapGateway(), CardVaultGateway)


@respx.mock
def test_create_checkout_plants_reference_and_customer() -> None:
    route = respx.post(CHARGES).mock(return_value=httpx.Response(200, json=_charge()))

    session = TapGateway().create_checkout(request=_request())

    assert session.charge_id == "chg_1"
    assert session.checkout_url == "https://pay.tap/x"
    assert session.outcome is None  # the hosted page + webhook settle it
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    payload = json.loads(request.content)
    assert payload["reference"]["transaction"] == "ref-123"
    assert payload["amount"] == "50.00"
    assert payload["customer"] == {
        "first_name": "Omar Gawdat",
        "email": "omar@example.com",
        "phone": {"country_code": 966, "number": 501234567},
    }


@respx.mock
def test_create_checkout_5xx_is_not_retried() -> None:
    """POST uses connect-only retry - a 500 may have created the charge."""
    route = respx.post(CHARGES).mock(return_value=httpx.Response(500))

    with pytest.raises(OutboundStatusError):
        TapGateway().create_checkout(request=_request())

    assert route.call_count == 1


@respx.mock
def test_create_checkout_2xx_without_url_fails() -> None:
    respx.post(CHARGES).mock(
        return_value=httpx.Response(200, json=_charge(transaction={"created": "1"}))
    )

    with pytest.raises(GatewayResponseError, match=r"transaction\.url"):
        TapGateway().create_checkout(request=_request())


@pytest.mark.parametrize("missing", ["id", "status", "currency", "amount"])
@respx.mock
def test_create_checkout_2xx_with_a_missing_field_names_it(missing: str) -> None:
    payload = _charge()
    del payload[missing]
    respx.post(CHARGES).mock(return_value=httpx.Response(200, json=payload))

    with pytest.raises(GatewayResponseError, match=missing):
        TapGateway().create_checkout(request=_request())


def _webhook_payload() -> dict[str, Any]:
    return _charge(status="CAPTURED", transaction={"created": "1760000000000"})


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


def _parse(payload: dict[str, Any], *, signature: str | None = None) -> Any:
    return TapGateway().parse_webhook(
        headers={"hashstring": signature or _sign(payload)},
        params={},
        body=json.dumps(payload).encode(),
    )


def test_webhook_with_valid_hashstring_parses() -> None:
    event = _parse(_webhook_payload())

    assert isinstance(event, PaymentEvent)
    assert event.reference == "ref-123"
    assert event.transaction_id == "chg_1"
    assert event.is_paid is True
    assert event.is_pending is False
    # The signed amount/currency ride along for the service's cross-check.
    assert event.amount_minor == 5000
    assert event.currency == "SAR"
    assert event.saved_card is None


def test_webhook_with_a_pending_status_is_informational() -> None:
    event = _parse(_webhook_payload() | {"status": "IN_PROGRESS"})

    assert event.is_paid is False
    assert event.is_pending is True


def test_webhook_with_a_declined_status_is_failed() -> None:
    event = _parse(_webhook_payload() | {"status": "DECLINED"})

    assert event.is_paid is False
    assert event.is_pending is False


def test_webhook_with_tampered_amount_is_rejected() -> None:
    payload = _webhook_payload()
    signature = _sign(payload)
    payload["amount"] = 1.0  # attacker rewrites after signing

    with pytest.raises(WebhookVerificationError):
        _parse(payload, signature=signature)


def test_webhook_without_hashstring_is_rejected() -> None:
    with pytest.raises(WebhookVerificationError):
        TapGateway().parse_webhook(
            headers={}, params={}, body=json.dumps(_webhook_payload()).encode()
        )


@pytest.mark.parametrize("body", [b"[]", b"null", b'"text"', b"42"])
def test_webhook_with_non_object_body_is_rejected(body: bytes) -> None:
    """Valid JSON that is not an object is a clean rejection, not a 500."""
    with pytest.raises(WebhookVerificationError):
        TapGateway().parse_webhook(
            headers={"hashstring": _sign(_webhook_payload())}, params={}, body=body
        )


@pytest.mark.parametrize(
    "missing", ["id", "amount", "currency", "status", "reference", "transaction"]
)
def test_webhook_lacking_a_signed_field_is_rejected_naming_it(missing: str) -> None:
    """A payload without a hashstring field was never signed by Tap - the
    check refuses it by name instead of hashing a blank in its place."""
    payload = _webhook_payload()
    signature = _sign(payload)
    del payload[missing]

    with pytest.raises(WebhookVerificationError, match=missing):
        _parse(payload, signature=signature)


def test_webhook_with_a_non_numeric_amount_raises() -> None:
    """No text fallback for the hashed amount: it is an arithmetic error."""
    payload = _webhook_payload()
    signature = _sign(payload)
    payload["amount"] = "fifty"

    with pytest.raises(InvalidOperation):
        _parse(payload, signature=signature)


@respx.mock
def test_fetch_status_maps_captured() -> None:
    respx.get(f"{CHARGES.rstrip('/')}/chg_1").mock(
        return_value=httpx.Response(200, json=_webhook_payload())
    )

    event = TapGateway().fetch_status(charge_id="chg_1", reference="ref-123")

    assert event is not None
    assert event.is_paid is True
    assert event.transaction_id == "chg_1"
    assert event.reference == "ref-123"
    assert event.amount_minor == 5000


@respx.mock
def test_refund_maps_status() -> None:
    respx.post("https://api.tap.company/v2/refunds/").mock(
        return_value=httpx.Response(200, json={"id": "re_1", "status": "PENDING"})
    )

    result = TapGateway().refund(
        transaction_id="chg_1", amount=Decimal("50.00"), currency="SAR"
    )

    # Accepted is not done: the row stays REFUND_PENDING until fetch_refund
    # reports REFUNDED.
    assert result.status == RefundStatus.PENDING
    assert result.refund_id == "re_1"


@respx.mock
def test_refund_without_a_status_is_loud() -> None:
    respx.post("https://api.tap.company/v2/refunds/").mock(
        return_value=httpx.Response(200, json={"id": "re_1"})
    )

    with pytest.raises(GatewayResponseError, match="status"):
        TapGateway().refund(
            transaction_id="chg_1", amount=Decimal("50.00"), currency="SAR"
        )


def _saved_request(*, agreement_id: str = "payment_agreement_555") -> CheckoutRequest:
    ref = SavedCardRef(
        token=CARD_REF.token,
        customer_id=CARD_REF.customer_id,
        agreement_id=agreement_id,
    )
    return _request(saved_card=ref)


@respx.mock
def test_create_checkout_always_saves_the_card() -> None:
    route = respx.post(CHARGES).mock(return_value=httpx.Response(200, json=_charge()))

    TapGateway().create_checkout(request=_request())

    payload = json.loads(route.calls.last.request.content)
    assert payload["save_card"] is True  # saving is not client-optional
    assert payload["threeDSecure"] is True  # saving requires 3DS


@respx.mock
def test_create_checkout_with_saved_card_creates_token_then_charge() -> None:
    token_route = respx.post(TOKENS).mock(
        return_value=httpx.Response(200, json={"id": "tok_once"})
    )
    charge_route = respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200,
            json=_charge(
                id="chg_2",
                transaction={"created": "1", "url": "https://pay.tap/3ds"},
            ),
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
    assert session.outcome is None


@respx.mock
def test_create_checkout_with_saved_card_captured_without_redirect() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200, json=_charge(id="chg_2", status="CAPTURED", transaction={})
        )
    )

    session = TapGateway().create_checkout(request=_saved_request())

    assert session.checkout_url == ""
    assert session.outcome is not None
    assert session.outcome.is_paid is True
    assert session.outcome.status == "CAPTURED"
    assert session.outcome.transaction_id == "chg_2"
    assert session.outcome.reference == "ref-123"
    assert session.outcome.amount_minor == 5000


@respx.mock
def test_charge_saved_is_mit_non_3ds() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    charge_route = respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200, json=_charge(id="chg_3", status="CAPTURED", transaction={})
        )
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
    assert session.outcome is not None
    assert session.outcome.is_paid is True


@respx.mock
def test_charge_saved_declined_maps_failed_result() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200, json=_charge(id="chg_4", status="DECLINED", transaction={})
        )
    )

    session = TapGateway().charge_saved(request=_saved_request())

    assert session.checkout_url == ""
    assert session.outcome is not None
    assert session.outcome.is_paid is False
    assert session.outcome.status == "DECLINED"


@respx.mock
def test_charge_saved_still_in_progress_has_no_outcome_yet() -> None:
    respx.post(TOKENS).mock(return_value=httpx.Response(200, json={"id": "tok_once"}))
    respx.post(CHARGES).mock(
        return_value=httpx.Response(
            200, json=_charge(id="chg_5", status="IN_PROGRESS", transaction={})
        )
    )

    session = TapGateway().charge_saved(request=_saved_request())

    assert session.checkout_url == ""
    assert session.outcome is None  # the webhook settles it


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

    event = _parse(payload)

    assert event.saved_card is not None
    assert event.saved_card.token == "card_abc123"
    assert event.saved_card.customer_id == "cus_xyz789"
    assert event.saved_card.agreement_id == "payment_agreement_555"
    assert event.saved_card.brand == "VISA"
    assert event.saved_card.last4 == "1019"
    assert event.saved_card.email == "omar@example.com"
    assert event.saved_card.exp_month is None


def test_webhook_saved_card_without_an_agreement_serves_cit_only() -> None:
    payload = _webhook_payload()
    payload["card"] = {"id": "card_abc123", "brand": "VISA", "last_four": "1019"}
    payload["customer"] = {"id": "cus_xyz789"}

    event = _parse(payload)

    assert event.saved_card is not None
    assert event.saved_card.agreement_id == ""
    assert event.saved_card.email == ""


def test_webhook_saved_card_without_a_customer_is_malformed() -> None:
    """A vaulted card lives under a Tap customer - charging/deleting it
    needs that id, so a payload without one is refused by name."""
    payload = _webhook_payload()
    payload["card"] = {"id": "card_abc123", "brand": "VISA", "last_four": "1019"}

    with pytest.raises(GatewayResponseError, match="customer"):
        _parse(payload)


def test_webhook_without_stored_card_has_no_saved_card_payload() -> None:
    """A transient card echo (no card_ id) must not look like a vaulted one."""
    payload = _webhook_payload()
    payload["card"] = {"id": "tok_transient", "brand": "VISA", "last_four": "1019"}

    assert _parse(payload).saved_card is None


@respx.mock
def test_create_checkout_reuses_the_customer_the_cards_live_under() -> None:
    route = respx.post(CHARGES).mock(return_value=httpx.Response(200, json=_charge()))

    TapGateway().create_checkout(request=_request(customer_id="cus_xyz789"))

    body = json.loads(route.calls[0].request.content)
    assert body["customer"]["id"] == "cus_xyz789"
    assert body["customer"]["email"] == "omar@example.com"  # still sent


@respx.mock
def test_create_checkout_without_a_customer_lets_tap_create_one() -> None:
    route = respx.post(CHARGES).mock(return_value=httpx.Response(200, json=_charge()))

    TapGateway().create_checkout(request=_request())

    body = json.loads(route.calls[0].request.content)
    assert "id" not in body["customer"]


@respx.mock
def test_saved_card_fingerprint_reads_the_card_api() -> None:
    route = respx.get(CARD_URL).mock(
        return_value=httpx.Response(
            200, json={"id": "card_abc123", "fingerprint": "fp/abc="}
        )
    )

    assert TapGateway().saved_card_fingerprint(saved_card=CARD_REF) == "fp/abc="
    assert route.call_count == 1


@pytest.mark.parametrize("status_code", [404, 500])
@respx.mock
def test_saved_card_fingerprint_raises_when_the_lookup_fails(
    status_code: int,
) -> None:
    respx.get(CARD_URL).mock(return_value=httpx.Response(status_code, json={}))

    with pytest.raises(OutboundStatusError):
        TapGateway().saved_card_fingerprint(saved_card=CARD_REF)


@respx.mock
def test_saved_card_fingerprint_raises_when_tap_omits_it() -> None:
    respx.get(CARD_URL).mock(return_value=httpx.Response(200, json={"id": "x"}))

    with pytest.raises(GatewayResponseError, match="fingerprint"):
        TapGateway().saved_card_fingerprint(saved_card=CARD_REF)


@respx.mock
def test_delete_saved_card_calls_gateway() -> None:
    route = respx.delete(CARD_URL).mock(
        return_value=httpx.Response(200, json={"deleted": True})
    )

    TapGateway().delete_saved_card(saved_card=CARD_REF)

    assert route.call_count == 1


@pytest.mark.parametrize("body", [{"deleted": False}, {"id": "card_abc123"}, []])
@respx.mock
def test_delete_saved_card_requires_an_explicit_confirmation(body: Any) -> None:
    """Only ``deleted: true`` counts - an absent flag is not a deletion."""
    respx.delete(CARD_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(GatewayResponseError):
        TapGateway().delete_saved_card(saved_card=CARD_REF)
