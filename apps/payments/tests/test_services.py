"""Services: checkout lifecycle, idempotent transitions, wallet integrity."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx
from django.core.management import call_command
from django.utils import timezone
from pydantic import SecretStr

from apps.common.http import OutboundTransportError
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.payments import selectors
from apps.payments import services
from apps.payments.constants import Currency
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import InsufficientBalanceError
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.exceptions import PaymentNotRefundableError
from apps.payments.exceptions import WalletCurrencyMismatchError
from apps.payments.exceptions import WalletNotFoundError
from apps.payments.gateways.base import ChargeStatus
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.fake import FakeGateway
from apps.payments.models import Payment
from apps.payments.models import Wallet
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


def _paid_topup(**kwargs: Any) -> Payment:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP, **kwargs)
    services.payment_apply_gateway_event(
        gateway_name="fake", event=_paid_event(payment)
    )
    payment.refresh_from_db()
    return payment


def _refund_pending_topup(*, actor: Any) -> Payment:
    """PAID top-up put through the interlock only: the enqueued executor
    rides on_commit, which never fires inside the test transaction."""
    payment = _paid_topup()
    services.payment_refund_start(payment=payment, actor=actor)
    payment.refresh_from_db()
    return payment


def _age(payment: Payment, *, minutes: int) -> None:
    Payment.objects.filter(pk=payment.pk).update(
        updated_at=timezone.now() - timedelta(minutes=minutes)
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
    assert wallet.balance == Decimal(0)
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


def test_paid_other_kind_notifies_and_leaves_wallet_untouched() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.OTHER)

    services.payment_apply_gateway_event(
        gateway_name="fake", event=_paid_event(payment)
    )

    wallet = payment.user.wallet  # provisioned at signup, never credited
    assert wallet.balance == Decimal(0)
    assert not wallet.transactions.exists()
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


# --- payment_refund_start + executor task ---------------------------------------


def test_refund_start_flow_debits_wallet_and_refunds(
    django_capture_on_commit_callbacks: Any,
) -> None:
    staff = UserFactory.create(staff=True)
    payment = _paid_topup()

    with django_capture_on_commit_callbacks(execute=True):
        started = services.payment_refund_start(payment=payment, actor=staff)

    assert started.status == PaymentStatus.REFUND_PENDING  # what the admin sees
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED  # executor ran on commit
    assert payment.refund_attempted_at is not None  # audit of the provider call
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)
    kinds = list(wallet.transactions.values_list("kind", flat=True))
    assert kinds.count(WalletTransactionKind.REFUND) == 1


def test_refund_start_requires_paid_status() -> None:
    staff = UserFactory.create(staff=True)
    payment = PaymentFactory.create()  # PENDING

    with pytest.raises(PaymentNotRefundableError):
        services.payment_refund_start(payment=payment, actor=staff)


def test_refund_start_blocked_when_topup_was_spent() -> None:
    staff = UserFactory.create(staff=True)
    payment = _paid_topup()
    services.wallet_apply(  # user spends the credit
        wallet_id=payment.user.wallet.pk,
        amount=-payment.amount,
        kind=WalletTransactionKind.PAYMENT,
    )

    with pytest.raises(InsufficientBalanceError):
        services.payment_refund_start(payment=payment, actor=staff)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID  # interlock fully rolled back


def test_refund_gateway_hit_once_when_second_refund_races(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """A second refund landing mid-provider-call is rejected by the interlock."""
    staff = UserFactory.create(staff=True)
    payment = _paid_topup()
    gateway_calls: list[str] = []

    def racing_refund(
        self: FakeGateway, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        gateway_calls.append(transaction_id)
        # The double-click: a second refund arrives while this one is in
        # flight - it must fail the PAID check, never reach the gateway.
        with pytest.raises(PaymentNotRefundableError):
            services.payment_refund_start(
                payment=Payment.objects.get(pk=payment.pk), actor=staff
            )
        return RefundResult(ok=True, raw={"fake": True})

    monkeypatch.setattr(FakeGateway, "refund", racing_refund)

    with django_capture_on_commit_callbacks(execute=True):
        services.payment_refund_start(payment=payment, actor=staff)

    assert gateway_calls == ["txn_1"]  # provider refunded exactly once
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)  # debited exactly once


def test_refund_reverts_wallet_and_status_when_gateway_rejects(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    staff = UserFactory.create(staff=True)
    payment = _paid_topup()
    monkeypatch.setattr(
        FakeGateway,
        "refund",
        lambda self, **kwargs: RefundResult(ok=False, raw={"fake": True}),
    )

    # The executor's PaymentRefundFailedError lands in the task result
    # (FAILED row in prod); the observable contract is the DB end state.
    with django_capture_on_commit_callbacks(execute=True):
        services.payment_refund_start(payment=payment, actor=staff)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID  # restored, refundable again
    assert payment.refund_attempted_at is None  # marker cleared with the revert
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == payment.amount  # debit compensated
    kinds = list(wallet.transactions.values_list("kind", flat=True))
    assert kinds.count(WalletTransactionKind.REFUND) == 1
    assert kinds.count(WalletTransactionKind.ADJUSTMENT) == 1


def test_refund_execute_is_a_noop_unless_refund_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replayed task after finalization must not touch the provider."""
    payment = _paid_topup()
    gateway_calls: list[str] = []
    monkeypatch.setattr(
        FakeGateway,
        "refund",
        lambda self, **kwargs: gateway_calls.append(kwargs["transaction_id"]),
    )

    result = services.payment_refund_execute(payment_id=payment.pk)

    assert result.status == PaymentStatus.PAID  # untouched
    assert gateway_calls == []


def test_refund_execute_never_recontacts_provider_after_an_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gateway.refund is not idempotent at the provider: once the marker is
    set, re-entry must reconcile manually, never re-send."""
    staff = UserFactory.create(staff=True)
    payment = _refund_pending_topup(actor=staff)
    Payment.objects.filter(pk=payment.pk).update(refund_attempted_at=timezone.now())
    gateway_calls: list[str] = []
    monkeypatch.setattr(
        FakeGateway,
        "refund",
        lambda self, **kwargs: gateway_calls.append(kwargs["transaction_id"]),
    )

    result = services.payment_refund_execute(payment_id=payment.pk, actor=staff)

    assert gateway_calls == []
    assert result.status == PaymentStatus.REFUND_PENDING  # awaiting a human


def test_refund_execute_reverts_when_request_provably_never_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff = UserFactory.create(staff=True)
    payment = _refund_pending_topup(actor=staff)

    def connect_failure(self: FakeGateway, **kwargs: Any) -> RefundResult:
        raise OutboundTransportError(
            service="fake", detail="connect refused", request_sent=False
        )

    monkeypatch.setattr(FakeGateway, "refund", connect_failure)

    with pytest.raises(PaymentGatewayUnavailableError):
        services.payment_refund_execute(payment_id=payment.pk, actor=staff)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID  # safe to retry
    assert payment.refund_attempted_at is None
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == payment.amount  # debit compensated


def test_refund_execute_holds_refund_pending_when_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read timeout may mean the provider DID refund: reverting would
    risk paying the user twice, so the row stays parked for reconciliation."""
    staff = UserFactory.create(staff=True)
    payment = _refund_pending_topup(actor=staff)

    def read_timeout(self: FakeGateway, **kwargs: Any) -> RefundResult:
        raise OutboundTransportError(
            service="fake", detail="read timeout", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, "refund", read_timeout)

    with pytest.raises(OutboundTransportError):
        services.payment_refund_execute(payment_id=payment.pk, actor=staff)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUND_PENDING
    assert payment.refund_attempted_at is not None
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)  # debit NOT re-credited


# --- wallet invariants -----------------------------------------------------------


def test_wallet_get_requires_provisioned_wallet() -> None:
    user = UserFactory.create()
    Wallet.objects.filter(user=user).delete()  # break the signup invariant

    with pytest.raises(WalletNotFoundError):
        selectors.wallet_get(user=user)


def test_initiate_topup_rejects_mismatched_wallet_currency() -> None:
    wallet = WalletFactory.create(currency=Currency.SAR)

    with pytest.raises(WalletCurrencyMismatchError):
        services.payment_initiate(
            user=wallet.user,
            amount=Decimal("10.00"),
            currency=Currency.EGP,
            kind=PaymentKind.WALLET_TOPUP,
        )

    # Rejected before the provider was asked for a checkout: no row at all.
    assert not Payment.objects.filter(user=wallet.user).exists()


def test_paid_event_rejects_mismatched_wallet_currency() -> None:
    """Credit-time backstop: a mismatched payment that bypassed initiate
    (crafted row, currency changed later) must never credit the wallet."""
    payment = PaymentFactory.create(
        kind=PaymentKind.WALLET_TOPUP, currency=Currency.EGP
    )  # the factory user's signup wallet is SAR

    with pytest.raises(WalletCurrencyMismatchError):
        services.payment_apply_gateway_event(
            gateway_name="fake", event=_paid_event(payment)
        )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING  # transition rolled back
    wallet = payment.user.wallet
    assert wallet.balance == Decimal(0)
    assert not wallet.transactions.exists()


# --- simulate_payment_webhook (the local Mailpit-for-payments) -------------------


def test_simulate_command_drives_the_real_transition() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    call_command("simulate_payment_webhook", str(payment.pk))

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount


# --- reconcile_payments (the recovery sweep) -------------------------------------


def test_reconcile_verifies_stale_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A payment whose webhook was lost is settled from the provider's answer."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    _age(payment, minutes=60)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_status",
        lambda self, **kwargs: ChargeStatus(
            transaction_id="txn_9", is_paid=True, status="CAPTURED", raw={}
        ),
    )

    call_command("reconcile_payments")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount


def test_reconcile_completes_unattempted_stale_refund() -> None:
    """Interlock committed but the executor task was lost: the sweep is the
    retry mechanism (django.tasks has none of its own)."""
    staff = UserFactory.create(staff=True)
    payment = _refund_pending_topup(actor=staff)
    _age(payment, minutes=60)

    call_command("reconcile_payments")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED


def test_reconcile_reports_attempted_refund_without_recontacting_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff = UserFactory.create(staff=True)
    payment = _refund_pending_topup(actor=staff)
    Payment.objects.filter(pk=payment.pk).update(refund_attempted_at=timezone.now())
    _age(payment, minutes=60)
    gateway_calls: list[str] = []
    monkeypatch.setattr(
        FakeGateway,
        "refund",
        lambda self, **kwargs: gateway_calls.append(kwargs["transaction_id"]),
    )

    call_command("reconcile_payments")

    assert gateway_calls == []  # never re-sent to the provider
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUND_PENDING  # awaiting a human


def test_reconcile_leaves_fresh_rows_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    # NOTE: --reuse-db keeps committed rows from the admin-gate session
    # fixture around, so assertions are scoped to THIS test's rows rather
    # than "the provider was never contacted at all".
    staff = UserFactory.create(staff=True)
    fresh_pending = PaymentFactory.create()
    fresh_refunding = _refund_pending_topup(actor=staff)
    checked_references: list[str] = []

    def recording_fetch(self: FakeGateway, **kwargs: Any) -> ChargeStatus:
        checked_references.append(kwargs["reference"])
        return ChargeStatus(transaction_id="", is_paid=False, status="pending", raw={})

    monkeypatch.setattr(FakeGateway, "fetch_status", recording_fetch)

    call_command("reconcile_payments")

    assert str(fresh_pending.idempotency_key) not in checked_references
    fresh_pending.refresh_from_db()
    fresh_refunding.refresh_from_db()
    assert fresh_pending.status == PaymentStatus.PENDING
    assert fresh_refunding.status == PaymentStatus.REFUND_PENDING
    assert fresh_refunding.refund_attempted_at is None  # executor never ran on it
