"""Services: checkout lifecycle, idempotent transitions, wallet integrity."""

from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from django.core.management import call_command
from pydantic import SecretStr

from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.payments import services
from apps.payments.constants import Currency
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import InsufficientBalanceError
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.exceptions import PaymentNotRefundableError
from apps.payments.gateways.base import WebhookEvent
from apps.payments.models import WalletTransaction
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.factories import WalletFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _paid_event(payment: Any) -> WebhookEvent:
    return WebhookEvent(
        reference=str(payment.idempotency_key),
        transaction_id="txn_1",
        is_paid=True,
        status="PAID",
        raw={"probe": True},
    )


# --- payment_initiate -----------------------------------------------------------


def test_initiate_returns_checkout_url_via_fake_gateway() -> None:
    user = UserFactory.create()

    payment = services.payment_initiate(
        user=user,
        amount=Decimal("50.00"),
        currency=Currency.SAR,
        kind=PaymentKind.WALLET_TOPUP,
        description="Top-up",
    )

    assert payment.status == PaymentStatus.PENDING
    assert payment.gateway == "fake"
    assert payment.gateway_charge_id == f"fake_charge_{payment.idempotency_key}"
    assert "/fake-checkout/" in payment.checkout_url


@respx.mock
def test_initiate_marks_failed_when_gateway_is_down(settings: Any) -> None:
    settings.PAYMENT_GATEWAYS = {"SAR": "apps.payments.gateways.tap.TapGateway"}
    settings.TAP_SECRET_KEY = SecretStr("sk_test")
    respx.post("https://api.tap.company/v2/charges/").mock(
        side_effect=httpx.ConnectError("down")
    )
    user = UserFactory.create()

    with pytest.raises(PaymentGatewayUnavailableError) as excinfo:
        services.payment_initiate(
            user=user, amount=Decimal("10.00"), currency=Currency.SAR
        )

    assert excinfo.value.status_code == 503
    payment = user.payments.get()
    assert payment.status == PaymentStatus.FAILED


# --- wallet_apply ---------------------------------------------------------------


def test_wallet_apply_moves_balance_and_appends_ledger() -> None:
    wallet = WalletFactory.create()

    first = services.wallet_apply(
        wallet_id=wallet.pk,
        amount=Decimal("30.00"),
        kind=WalletTransactionKind.TOPUP,
    )
    second = services.wallet_apply(
        wallet_id=wallet.pk,
        amount=Decimal("-10.00"),
        kind=WalletTransactionKind.PAYMENT,
    )

    wallet.refresh_from_db()
    assert wallet.balance == Decimal("20.00")
    assert first.balance_after == Decimal("30.00")
    assert second.balance_after == Decimal("20.00")


def test_wallet_apply_blocks_overdraft() -> None:
    wallet = WalletFactory.create()

    with pytest.raises(InsufficientBalanceError):
        services.wallet_apply(
            wallet_id=wallet.pk,
            amount=Decimal("-1.00"),
            kind=WalletTransactionKind.PAYMENT,
        )

    wallet.refresh_from_db()
    assert wallet.balance == Decimal("0")
    assert not WalletTransaction.objects.filter(wallet=wallet).exists()


# --- payment_apply_gateway_event ------------------------------------------------


def test_paid_topup_credits_wallet_and_notifies_exactly_once() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    event = _paid_event(payment)

    services.payment_apply_gateway_event(gateway_name="fake", event=event)
    replayed = services.payment_apply_gateway_event(gateway_name="fake", event=event)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.paid_at is not None
    assert payment.gateway_transaction_id == "txn_1"
    assert replayed.status == PaymentStatus.PAID  # replay: recorded, no re-credit
    wallet = payment.user.wallet
    assert wallet.balance == payment.amount
    assert wallet.transactions.count() == 1  # the replay did NOT double credit
    assert (
        Notification.objects.filter(
            recipient=payment.user, kind=NotificationKind.WALLET_CREDITED
        ).count()
        == 1
    )


def test_paid_other_kind_notifies_without_wallet() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.OTHER)

    services.payment_apply_gateway_event(
        gateway_name="fake", event=_paid_event(payment)
    )

    assert not hasattr(payment.user, "wallet") or not payment.user.wallet
    assert Notification.objects.filter(
        recipient=payment.user, kind=NotificationKind.PAYMENT_PAID
    ).exists()


def test_failed_event_marks_failed() -> None:
    payment = PaymentFactory.create()
    event = WebhookEvent(
        reference=str(payment.idempotency_key),
        transaction_id="txn_2",
        is_paid=False,
        status="FAILED",
        raw={},
    )

    services.payment_apply_gateway_event(gateway_name="fake", event=event)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED


def test_unknown_reference_is_a_404() -> None:
    event = WebhookEvent(
        reference="not-a-known-reference",
        transaction_id="x",
        is_paid=True,
        status="PAID",
        raw={},
    )

    with pytest.raises(PaymentNotFoundError):
        services.payment_apply_gateway_event(gateway_name="fake", event=event)


# --- payment_refund -------------------------------------------------------------


def test_refund_of_paid_topup_debits_wallet() -> None:
    staff = UserFactory.create(staff=True)
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name="fake", event=_paid_event(payment)
    )
    payment.refresh_from_db()

    refunded = services.payment_refund(payment=payment, actor=staff)

    assert refunded.status == PaymentStatus.REFUNDED
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal("0")
    kinds = list(wallet.transactions.values_list("kind", flat=True))
    assert kinds.count(WalletTransactionKind.REFUND) == 1


def test_refund_requires_paid_status() -> None:
    staff = UserFactory.create(staff=True)
    payment = PaymentFactory.create()  # PENDING

    with pytest.raises(PaymentNotRefundableError):
        services.payment_refund(payment=payment, actor=staff)


def test_refund_blocked_when_topup_was_spent() -> None:
    staff = UserFactory.create(staff=True)
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name="fake", event=_paid_event(payment)
    )
    payment.refresh_from_db()
    services.wallet_apply(  # user spends the credit
        wallet_id=payment.user.wallet.pk,
        amount=-payment.amount,
        kind=WalletTransactionKind.PAYMENT,
    )

    with pytest.raises(InsufficientBalanceError):
        services.payment_refund(payment=payment, actor=staff)


# --- simulate_payment_webhook (the local Mailpit-for-payments) -------------------


def test_simulate_command_drives_the_real_transition() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    call_command("simulate_payment_webhook", str(payment.pk))

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount
