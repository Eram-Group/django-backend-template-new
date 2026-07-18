"""Checkout, wallet, and webhook endpoints."""

import json
from decimal import Decimal

import pytest
from django.test import Client

from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.tests.factories import PaymentFactory
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
