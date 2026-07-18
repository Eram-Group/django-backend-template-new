"""Checkout, wallet, and webhook endpoints."""

import json
from decimal import Decimal

import pytest
from django.test import Client

from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.models import SavedCard
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.factories import SavedCardFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PAYMENTS = "/api/v1/payments"
FAKE_WEBHOOK = "/api/v1/payments/webhooks/fake"
SIGNATURE = {"X-Fake-Signature": "fake-signature"}


def test_endpoints_require_auth(client: Client) -> None:
    assert client.get(f"{PAYMENTS}/").status_code == 401
    assert client.get(f"{PAYMENTS}/wallet").status_code == 401


def test_checkout_roundtrip(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)

    response = client.post(
        f"{PAYMENTS}/",
        {"amount": "50.00", "currency": "SAR", "kind": "wallet_topup"},
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert "/fake-checkout/" in body["checkout_url"]

    detail = client.get(f"{PAYMENTS}/{body['id']}").json()
    assert detail["id"] == body["id"]

    listing = client.get(f"{PAYMENTS}/").json()
    assert len(listing["items"]) == 1


def test_amount_must_be_positive(client: Client) -> None:
    client.force_login(UserFactory.create())

    response = client.post(
        f"{PAYMENTS}/",
        {"amount": "-5", "currency": "SAR"},
        content_type="application/json",
    )

    assert response.status_code == 422


def test_payments_are_scoped_to_their_owner(client: Client) -> None:
    other = PaymentFactory.create()
    user = UserFactory.create()
    client.force_login(user)

    response = client.get(f"{PAYMENTS}/{other.pk}")

    assert response.status_code == 404


# --- webhook ---------------------------------------------------------------------


def _webhook_body(payment: object) -> str:
    return json.dumps(
        {"reference": str(payment.idempotency_key), "paid": True}  # type: ignore[attr-defined]
    )


def test_webhook_marks_paid_and_credits_wallet_once(client: Client) -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    first = client.post(
        FAKE_WEBHOOK,
        _webhook_body(payment),
        content_type="application/json",
        headers=SIGNATURE,
    )
    replay = client.post(
        FAKE_WEBHOOK,
        _webhook_body(payment),
        content_type="application/json",
        headers=SIGNATURE,
    )

    assert first.status_code == 200
    assert replay.status_code == 200  # replays acknowledge, never re-credit
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    wallet = payment.user.wallet
    assert wallet.balance == payment.amount
    assert wallet.transactions.count() == 1


def test_webhook_with_bad_signature_is_400(client: Client) -> None:
    payment = PaymentFactory.create()

    response = client.post(
        FAKE_WEBHOOK,
        _webhook_body(payment),
        content_type="application/json",
        headers={"X-Fake-Signature": "forged"},
    )

    assert response.status_code == 400
    assert response.json()["extra"]["code"] == "webhook_rejected"
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


def test_webhook_for_unknown_gateway_is_400(client: Client) -> None:
    response = client.post(
        f"{PAYMENTS}/webhooks/nope",
        "{}",
        content_type="application/json",
    )

    assert response.status_code == 400


def test_webhook_for_unknown_payment_is_404(client: Client) -> None:
    response = client.post(
        FAKE_WEBHOOK,
        json.dumps({"reference": "ghost", "paid": True}),
        content_type="application/json",
        headers=SIGNATURE,
    )

    assert response.status_code == 404


# --- wallet ----------------------------------------------------------------------


def test_wallet_shows_credited_balance_and_ledger(client: Client) -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    client.post(
        FAKE_WEBHOOK,
        _webhook_body(payment),
        content_type="application/json",
        headers=SIGNATURE,
    )
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
    user = UserFactory.create()
    card = SavedCardFactory.create(user=user)
    client.force_login(user)

    response = client.post(
        f"{PAYMENTS}/",
        {
            "amount": "50.00",
            "currency": "SAR",
            "kind": "wallet_topup",
            "saved_card_id": str(card.pk),
        },
        content_type="application/json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paid"  # FakeGateway one-click captures instantly
    assert body["checkout_url"] == ""  # nothing to redirect to
    assert body["saved_card_id"] == str(card.pk)


def test_checkout_with_unknown_saved_card_is_404(client: Client) -> None:
    client.force_login(UserFactory.create())

    response = client.post(
        f"{PAYMENTS}/",
        {
            "amount": "50.00",
            "currency": "SAR",
            "saved_card_id": "00000000-0000-0000-0000-000000000000",
        },
        content_type="application/json",
    )

    assert response.status_code == 404


def test_webhook_token_event_stores_card(client: Client) -> None:
    user = UserFactory.create()

    response = client.post(
        FAKE_WEBHOOK,
        json.dumps({"card_token": "tok_hosted_1", "email": user.email}),
        content_type="application/json",
        headers=SIGNATURE,
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    card = SavedCard.objects.get(token="tok_hosted_1")  # noqa: S106 - fixture
    assert card.user == user
    assert card.token == "tok_hosted_1"  # noqa: S105 - test fixture value


def test_webhook_token_event_with_bad_signature_is_400(client: Client) -> None:
    response = client.post(
        FAKE_WEBHOOK,
        json.dumps({"card_token": "tok_hosted_1", "email": "x@example.com"}),
        content_type="application/json",
        headers={"X-Fake-Signature": "forged"},
    )

    assert response.status_code == 400
    assert not SavedCard.objects.filter(token="tok_hosted_1").exists()  # noqa: S106
