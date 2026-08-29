"""Checkout, wallet, and webhook endpoints."""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client
from structlog.testing import capture_logs

from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.factories import SavedCardFactory
from apps.payments.tests.fake_gateway import SIGNATURE
from apps.payments.tests.fake_gateway import SIGNATURE_HEADER
from apps.users.models import User
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PAYMENTS = "/api/v1/payments"
#: The FakeGateway answers to Tap's name - its webhook lands on Tap's URL.
WEBHOOK = f"{PAYMENTS}/webhooks/{GatewayName.TAP}"
SIGNED = {SIGNATURE_HEADER: SIGNATURE}
PHONE = "+966501234567"


def _payer(**kwargs: Any) -> User:
    return UserFactory.create(phone=PHONE, **kwargs)


def _checkout(client: Client, **overrides: Any) -> Any:
    body: dict[str, Any] = {
        "amount": "50.00",
        "currency": "SAR",
        "kind": "wallet_topup",
    }
    body.update(overrides)
    return client.post(f"{PAYMENTS}/", body, content_type="application/json")


def test_endpoints_require_auth(client: Client) -> None:
    assert client.get(f"{PAYMENTS}/").status_code == 401
    assert client.get(f"{PAYMENTS}/wallet").status_code == 401


def test_checkout_roundtrip(client: Client) -> None:
    user = _payer()
    client.force_login(user)

    response = _checkout(client)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert "/fake-checkout/" in body["checkout_url"]

    detail = client.get(f"{PAYMENTS}/{body['id']}").json()
    assert detail["id"] == body["id"]

    listing = client.get(f"{PAYMENTS}/").json()
    assert len(listing["items"]) == 1


def test_amount_must_be_positive(client: Client) -> None:
    client.force_login(_payer())

    assert _checkout(client, amount="-5").status_code == 422


def test_kind_is_required(client: Client) -> None:
    """The client says what the money is for - no default kind."""
    client.force_login(_payer())

    response = client.post(
        f"{PAYMENTS}/",
        {"amount": "50.00", "currency": "SAR"},
        content_type="application/json",
    )

    assert response.status_code == 422


def test_checkout_without_customer_details_is_refused(client: Client) -> None:
    user = UserFactory.create(phone="")
    client.force_login(user)

    response = _checkout(client)

    assert response.status_code == 400
    assert response.json()["extra"]["code"] == "customer_details_required"
    assert not Payment.objects.filter(user=user).exists()


def test_payments_are_scoped_to_their_owner(client: Client) -> None:
    other = PaymentFactory.create()
    user = UserFactory.create()
    client.force_login(user)

    response = client.get(f"{PAYMENTS}/{other.pk}")

    assert response.status_code == 404


# --- webhook ---------------------------------------------------------------------


def _webhook_body(payment: Payment, **overrides: Any) -> str:
    body: dict[str, Any] = {
        "reference": str(payment.idempotency_key),
        "paid": True,
        "amount_minor": int(payment.amount * 100),
        "currency": str(payment.currency),
    }
    body.update(overrides)
    return json.dumps(body)


def _post_webhook(client: Client, body: str, **headers: str) -> Any:
    return client.post(
        WEBHOOK, body, content_type="application/json", headers=headers or SIGNED
    )


def test_webhook_marks_paid_and_credits_wallet_once(client: Client) -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    first = _post_webhook(client, _webhook_body(payment))
    replay = _post_webhook(client, _webhook_body(payment))

    assert first.status_code == 200
    assert replay.status_code == 200  # replays acknowledge, never re-credit
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    wallet = payment.user.wallet
    assert wallet.balance == payment.amount
    assert wallet.transactions.count() == 1


def test_webhook_with_mismatched_amount_is_refused(client: Client) -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    response = _post_webhook(client, _webhook_body(payment, amount_minor=1))

    assert response.status_code == 400
    assert response.json()["extra"]["code"] == "payment_event_mismatch"
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    assert payment.user.wallet.balance == Decimal(0)


def test_webhook_with_bad_signature_is_400_and_logged(client: Client) -> None:
    """A rejected callback is money we did not record: it must be an ERROR
    log event (Sentry), not just a 400 in the access log."""
    payment = PaymentFactory.create()

    with capture_logs() as logs:
        response = _post_webhook(
            client, _webhook_body(payment), **{SIGNATURE_HEADER: "forged"}
        )

    assert response.status_code == 400
    assert response.json()["extra"]["code"] == "webhook_rejected"
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    rejected = [log for log in logs if log["event"] == "payment_webhook_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["log_level"] == "error"
    assert rejected[0]["gateway"] == GatewayName.TAP
    assert rejected[0]["reason"]
    assert "forged" not in str(rejected[0])  # never echo the posted signature


def test_webhook_for_unknown_gateway_is_400_and_logged(client: Client) -> None:
    with capture_logs() as logs:
        response = client.post(
            f"{PAYMENTS}/webhooks/nope",
            "{}",
            content_type="application/json",
        )

    assert response.status_code == 400
    assert response.json()["extra"]["code"] == "webhook_rejected"
    rejected = [log for log in logs if log["event"] == "payment_webhook_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["log_level"] == "error"
    assert rejected[0]["gateway"] == "nope"
    assert rejected[0]["reason"] == "unknown or unconfigured gateway"


def test_webhook_for_unknown_payment_is_404_and_logged(client: Client) -> None:
    payment = PaymentFactory.create()

    with capture_logs() as logs:
        response = _post_webhook(client, _webhook_body(payment, reference="ghost"))

    assert response.status_code == 404
    unknown = [log for log in logs if log["event"] == "payment_webhook_unknown_payment"]
    assert len(unknown) == 1
    assert unknown[0]["log_level"] == "warning"
    assert unknown[0]["gateway"] == GatewayName.TAP
    assert unknown[0]["reference"] == "ghost"
    assert not [log for log in logs if log["event"] == "payment_webhook_rejected"]


# --- wallet ----------------------------------------------------------------------


def test_wallet_shows_credited_balance_and_ledger(client: Client) -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    _post_webhook(client, _webhook_body(payment))
    client.force_login(payment.user)

    wallet = client.get(f"{PAYMENTS}/wallet").json()
    assert Decimal(str(wallet["balance"])) == payment.amount

    ledger = client.get(f"{PAYMENTS}/wallet/transactions").json()
    assert len(ledger["items"]) == 1
    assert ledger["items"][0]["kind"] == "topup"


# --- saved cards -----------------------------------------------------------------


def test_cards_require_auth(client: Client) -> None:
    assert client.get(f"{PAYMENTS}/cards").status_code == 401


def test_card_list_and_delete_roundtrip(client: Client) -> None:
    card = SavedCardFactory.create(brand="VISA", last4="1019")
    client.force_login(card.user)

    listing = client.get(f"{PAYMENTS}/cards").json()
    assert len(listing["items"]) == 1
    item = listing["items"][0]
    assert item["brand"] == "VISA"
    assert item["last4"] == "1019"
    assert "token" not in item  # gateway references never leave the server

    assert client.delete(f"{PAYMENTS}/cards/{card.pk}").status_code == 204
    assert client.get(f"{PAYMENTS}/cards").json()["items"] == []


def test_card_delete_is_scoped_to_owner(client: Client) -> None:
    other_card = SavedCardFactory.create()
    client.force_login(UserFactory.create())

    assert client.delete(f"{PAYMENTS}/cards/{other_card.pk}").status_code == 404
    assert SavedCard.objects.filter(pk=other_card.pk).exists()


def test_checkout_with_saved_card_id_pays_instantly(client: Client) -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user)
    client.force_login(user)

    response = _checkout(client, saved_card_id=str(card.pk))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paid"  # FakeGateway one-click captures instantly
    assert body["checkout_url"] == ""  # nothing to redirect to
    assert body["saved_card_id"] == str(card.pk)


def test_checkout_with_unknown_saved_card_is_404(client: Client) -> None:
    client.force_login(_payer())

    response = _checkout(client, saved_card_id="00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404


def test_webhook_token_event_stores_card(client: Client) -> None:
    user = UserFactory.create()

    response = _post_webhook(
        client, json.dumps({"card_token": "tok_hosted_1", "email": user.email})
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    card = SavedCard.objects.get(token="tok_hosted_1")
    assert card.user == user
    assert card.token == "tok_hosted_1"


def test_webhook_token_event_with_bad_signature_is_400(client: Client) -> None:
    response = _post_webhook(
        client,
        json.dumps({"card_token": "tok_hosted_1", "email": "x@example.com"}),
        **{SIGNATURE_HEADER: "forged"},
    )

    assert response.status_code == 400
    assert not SavedCard.objects.filter(token="tok_hosted_1").exists()


def test_checkout_is_throttled_per_user(client: Client) -> None:
    """10 checkouts a minute per user; the 11th is the 429 envelope and opens
    no gateway session. Another user is unaffected."""
    payer = _payer()
    client.force_login(payer)

    statuses = [_checkout(client).status_code for _ in range(11)]

    assert statuses[:10] == [200] * 10
    assert statuses[10] == 429
    assert _checkout(client).json() == {"message": "Too many requests.", "extra": {}}
    assert Payment.objects.filter(user=payer).count() == 10

    other = Client()
    other.force_login(_payer())
    assert _checkout(other).status_code == 200
