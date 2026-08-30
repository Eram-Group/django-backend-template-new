"""Services: checkout lifecycle, idempotent transitions, wallet integrity."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from apps.common.http import OutboundTransportError
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.payments import selectors
from apps.payments import services
from apps.payments.constants import CURRENCY_BY_LANGUAGE
from apps.payments.constants import Currency
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import CustomerDetailsRequiredError
from apps.payments.exceptions import InsufficientBalanceError
from apps.payments.exceptions import PaymentEventMismatchError
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.exceptions import PaymentNotRefundableError
from apps.payments.exceptions import SavedCardGatewayMismatchError
from apps.payments.exceptions import SavedCardNotFoundError
from apps.payments.exceptions import WalletCurrencyMismatchError
from apps.payments.exceptions import WalletNotFoundError
from apps.payments.gateways import GATEWAY_CLASSES
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways import gateway_for_currency
from apps.payments.gateways.base import CardTokenEvent
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import SavedCardData
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.factories import SavedCardFactory
from apps.payments.tests.factories import WalletFactory
from apps.payments.tests.fake_gateway import FakeGateway
from apps.users.constants import Language
from apps.users.models import User
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

#: The FakeGateway answers to Tap's name (test settings map every currency
#: to it) - Payment.gateway only admits real gateway names.
GATEWAY = GatewayName.TAP
PHONE = "+966501234567"


def _payer(**kwargs: Any) -> User:
    """A user who can check out: the service requires a full name (the
    factory's) and a phone (UserFactory sets none)."""
    return UserFactory.create(phone=PHONE, **kwargs)


class _VaultlessGateway:
    """Paymob's shape: a gateway with no ``CardVaultGateway`` capability.

    A separate class rather than a stripped FakeGateway - runtime Protocol
    checks are cached per class by ABCMeta, so a capability cannot be
    monkeypatched away once it has tested positive.
    """

    name = GATEWAY


@pytest.fixture
def vaultless_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(GATEWAY_CLASSES, GATEWAY, _VaultlessGateway)


def _event(payment: Payment, **overrides: Any) -> PaymentEvent:
    """A paid event matching the row's signed amount; overrides shape the
    informational / mismatching variants."""
    fields: dict[str, Any] = {
        "reference": str(payment.idempotency_key),
        "transaction_id": "txn_1",
        "is_paid": True,
        "is_pending": False,
        "status": "PAID",
        "amount_minor": int(payment.amount * 100),
        "currency": str(payment.currency),
        "saved_card": None,
        "raw": {"probe": True},
    }
    fields.update(overrides)
    return PaymentEvent(**fields)


def _paid_topup(**kwargs: Any) -> Payment:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP, **kwargs)
    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=_event(payment))
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


def _initiate(user: User, **overrides: Any) -> Payment:
    fields: dict[str, Any] = {
        "user": user,
        "amount": Decimal("50.00"),
        "currency": Currency.SAR,
        "kind": PaymentKind.WALLET_TOPUP,
        "description": "Top-up",
        "saved_card": None,
    }
    fields.update(overrides)
    return services.payment_initiate(**fields)


def _charge_saved(user: User, card: SavedCard, **overrides: Any) -> Payment:
    fields: dict[str, Any] = {
        "user": user,
        "saved_card": card,
        "amount": Decimal("25.00"),
        "currency": Currency.SAR,
        "kind": PaymentKind.WALLET_TOPUP,
        "description": "Top-up",
    }
    fields.update(overrides)
    return services.payment_charge_saved(**fields)


# --- gateway registry -----------------------------------------------------------


def test_unmapped_currency_raises_unavailable() -> None:
    with pytest.raises(PaymentGatewayUnavailableError) as excinfo:
        gateway_for_currency("USD")

    assert excinfo.value.status_code == 503


def test_unknown_gateway_name_raises_unavailable() -> None:
    with pytest.raises(PaymentGatewayUnavailableError):
        gateway_by_name("nope")


def test_mapped_currency_resolves_the_test_gateway() -> None:
    assert isinstance(gateway_for_currency("SAR"), FakeGateway)
    assert isinstance(gateway_by_name(GATEWAY), FakeGateway)


# --- payment_initiate -----------------------------------------------------------


def test_initiate_returns_checkout_url_via_fake_gateway() -> None:
    payment = _initiate(_payer())

    assert payment.status == PaymentStatus.PENDING
    assert payment.gateway == GATEWAY
    assert payment.gateway_charge_id == f"fake_charge_{payment.idempotency_key}"
    assert "/fake-checkout/" in payment.checkout_url


def test_initiate_marks_failed_when_gateway_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def down(self: FakeGateway, *, request: Any) -> CheckoutSession:
        raise OutboundTransportError(
            service="tap", detail="connect refused", request_sent=False
        )

    monkeypatch.setattr(FakeGateway, "create_checkout", down)
    user = _payer()

    with pytest.raises(PaymentGatewayUnavailableError) as excinfo:
        _initiate(user, amount=Decimal("10.00"))

    assert excinfo.value.status_code == 503
    payment = user.payments.get()
    assert payment.status == PaymentStatus.FAILED


@pytest.mark.parametrize(
    ("name", "phone"),
    [("Omar Gawdat", ""), ("Omar", PHONE), ("", PHONE), ("   ", PHONE)],
)
def test_initiate_requires_full_name_and_phone(name: str, phone: str) -> None:
    """Gateways bill first/last name and phone; nothing is made up in their
    place, so the checkout is refused before a row or a provider call."""
    user = UserFactory.create(name=name, phone=phone)

    with pytest.raises(CustomerDetailsRequiredError):
        _initiate(user)

    assert not Payment.objects.filter(user=user).exists()


def test_charge_saved_requires_customer_details() -> None:
    user = UserFactory.create(phone="")  # factory name is a full name
    card = SavedCardFactory.create(user=user)

    with pytest.raises(CustomerDetailsRequiredError):
        _charge_saved(user, card)


def test_initiate_passes_the_customer_details_to_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[CheckoutRequest] = []
    original = FakeGateway.create_checkout

    def capture(self: FakeGateway, *, request: CheckoutRequest) -> CheckoutSession:
        seen.append(request)
        return original(self, request=request)

    monkeypatch.setattr(FakeGateway, "create_checkout", capture)
    user = _payer(name="Omar Gawdat")

    _initiate(user)

    assert seen[0].customer_name == "Omar Gawdat"
    assert seen[0].customer_phone == PHONE
    assert seen[0].customer_email == user.email


# --- wallet_apply / wallet_create -----------------------------------------------


def test_wallet_apply_moves_balance_and_appends_ledger() -> None:
    wallet = WalletFactory.create()

    first = services.wallet_apply(
        wallet_id=wallet.pk,
        amount=Decimal("30.00"),
        kind=WalletTransactionKind.TOPUP,
        payment=None,
        actor=None,
        note="",
    )
    second = services.wallet_apply(
        wallet_id=wallet.pk,
        amount=Decimal("-10.00"),
        kind=WalletTransactionKind.PAYMENT,
        payment=None,
        actor=None,
        note="",
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
            payment=None,
            actor=None,
            note="",
        )

    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)
    assert not WalletTransaction.objects.filter(wallet=wallet).exists()


def test_currency_by_language_covers_every_language() -> None:
    """user_create derives the wallet currency from the language; a
    Language member without a mapping would break signup."""
    assert set(CURRENCY_BY_LANGUAGE) == set(Language)
    for language in Language:
        assert services.wallet_currency_for(language=language) in Currency


def test_wallet_currency_for_rejects_unknown_language() -> None:
    with pytest.raises(ValueError, match="not a valid Language"):
        services.wallet_currency_for(language="fr")


def test_wallet_create_uses_the_given_currency() -> None:
    user = UserFactory.create()
    Wallet.objects.filter(user=user).delete()

    wallet = services.wallet_create(user=user, currency=Currency.EGP)

    assert wallet.currency == Currency.EGP
    assert wallet.balance == 0


# --- payment_apply_gateway_event ------------------------------------------------


def test_paid_topup_credits_wallet_and_notifies_exactly_once() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    event = _event(payment)

    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=event)
    replayed = services.payment_apply_gateway_event(gateway_name=GATEWAY, event=event)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.paid_at is not None
    assert payment.gateway_transaction_id == "txn_1"
    assert replayed.status == PaymentStatus.PAID  # replay: recorded, no re-credit
    wallet = payment.user.wallet
    assert wallet.balance == payment.amount
    assert wallet.transactions.count() == 1  # the replay did NOT double credit
    notification = Notification.objects.get(
        recipient=payment.user, kind=NotificationKind.WALLET_CREDITED
    )  # get(): the replay did NOT double notify
    assert notification.context == {
        "amount": str(payment.amount),
        "currency": payment.currency,
        "balance": str(wallet.balance),  # wallet_apply's balance_after
    }


def test_paid_other_kind_leaves_wallet_untouched() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.OTHER)

    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=_event(payment))

    wallet = payment.user.wallet  # provisioned at signup, never credited
    assert wallet.balance == Decimal(0)
    assert not wallet.transactions.exists()
    notification = Notification.objects.get(
        recipient=payment.user, kind=NotificationKind.PAYMENT_PAID
    )
    assert notification.context == {
        "amount": str(payment.amount),
        "currency": payment.currency,
    }


def test_failed_event_marks_failed() -> None:
    payment = PaymentFactory.create()

    services.payment_apply_gateway_event(
        gateway_name=GATEWAY,
        event=_event(payment, transaction_id="txn_2", is_paid=False, status="FAILED"),
    )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.FAILED


def test_unknown_reference_is_a_404() -> None:
    payment = PaymentFactory.create()

    with pytest.raises(PaymentNotFoundError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY,
            event=_event(payment, reference="not-a-known-reference"),
        )


def test_event_for_another_gateway_is_a_404() -> None:
    payment = PaymentFactory.create(gateway=GatewayName.PAYMOB)

    with pytest.raises(PaymentNotFoundError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY, event=_event(payment)
        )


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


def test_refund_start_requires_a_settled_transaction_id() -> None:
    """The refund targets the settled transaction id - never the checkout
    id in its place."""
    staff = UserFactory.create(staff=True)
    payment = _paid_topup()
    Payment.objects.filter(pk=payment.pk).update(gateway_transaction_id="")

    with pytest.raises(PaymentNotRefundableError):
        services.payment_refund_start(payment=payment, actor=staff)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount  # nothing debited


def test_refund_start_blocked_when_topup_was_spent() -> None:
    staff = UserFactory.create(staff=True)
    payment = _paid_topup()
    services.wallet_apply(  # user spends the credit
        wallet_id=payment.user.wallet.pk,
        amount=-payment.amount,
        kind=WalletTransactionKind.PAYMENT,
        payment=None,
        actor=None,
        note="",
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

    result = services.payment_refund_execute(payment_id=payment.pk, actor=None)

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
            service="tap", detail="connect refused", request_sent=False
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
            service="tap", detail="read timeout", request_sent=True
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
    wallet = WalletFactory.create(user=_payer(), currency=Currency.SAR)

    with pytest.raises(WalletCurrencyMismatchError):
        _initiate(wallet.user, amount=Decimal("10.00"), currency=Currency.EGP)

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
            gateway_name=GATEWAY, event=_event(payment)
        )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING  # transition rolled back
    wallet = payment.user.wallet
    assert wallet.balance == Decimal(0)
    assert not wallet.transactions.exists()


# --- reconcile_payments (the recovery sweep) -------------------------------------


def test_reconcile_verifies_stale_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A payment whose webhook was lost is settled from the provider's answer."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    _age(payment, minutes=60)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_status",
        lambda self, **kwargs: _event(payment, transaction_id="txn_9"),
    )

    call_command("reconcile_payments")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.gateway_transaction_id == "txn_9"
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
    # NOTE: the admin-gate session fixture commits rows outside the test
    # transaction, so assertions are scoped to THIS test's rows rather
    # than "the provider was never contacted at all".
    staff = UserFactory.create(staff=True)
    fresh_pending = PaymentFactory.create()
    fresh_refunding = _refund_pending_topup(actor=staff)
    checked_references: list[str] = []

    def recording_fetch(self: FakeGateway, **kwargs: Any) -> None:
        checked_references.append(kwargs["reference"])

    monkeypatch.setattr(FakeGateway, "fetch_status", recording_fetch)

    call_command("reconcile_payments")

    assert str(fresh_pending.idempotency_key) not in checked_references
    fresh_pending.refresh_from_db()
    fresh_refunding.refresh_from_db()
    assert fresh_pending.status == PaymentStatus.PENDING
    assert fresh_refunding.status == PaymentStatus.REFUND_PENDING
    assert fresh_refunding.refund_attempted_at is None  # executor never ran on it


def test_reconcile_exits_non_zero_when_a_provider_call_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped row is retried next run, but the scheduled task must not
    report a clean sweep: the exit code is what alerts."""
    payment = PaymentFactory.create()
    _age(payment, minutes=60)

    def down(self: FakeGateway, **kwargs: Any) -> None:
        raise OutboundTransportError(
            service="tap", detail="connect refused", request_sent=False
        )

    monkeypatch.setattr(FakeGateway, "fetch_status", down)

    with pytest.raises(CommandError, match="1 provider calls failed"):
        call_command("reconcile_payments")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING  # next run retries it


def test_reconcile_counts_a_failed_refund_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staff = UserFactory.create(staff=True)
    payment = _refund_pending_topup(actor=staff)
    _age(payment, minutes=60)
    monkeypatch.setattr(
        FakeGateway,
        "refund",
        lambda self, **kwargs: RefundResult(ok=False, raw={"fake": True}),
    )

    with pytest.raises(CommandError, match="1 provider calls failed"):
        call_command("reconcile_payments")

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID  # interlock reverted, retryable


# --- saved cards -----------------------------------------------------------------


def _card_data(**overrides: Any) -> SavedCardData:
    fields: dict[str, Any] = {
        "token": "fake_card_A",
        "customer_id": "fake_cus_A",
        "agreement_id": "fake_agr_A",
        "brand": "VISA",
        "last4": "1019",
        "exp_month": 12,
        "exp_year": 2030,
        "email": "",
    }
    fields.update(overrides)
    return SavedCardData(**fields)


def test_initiate_always_requests_card_save() -> None:
    payment = _initiate(_payer())

    assert payment.save_card_requested is True  # saving is not client-optional
    assert "/fake-checkout/" in payment.checkout_url  # normal redirect flow


def test_paid_event_with_card_payload_stores_saved_card() -> None:
    payment = PaymentFactory.create(save_card_requested=True)
    event = _event(payment, saved_card=_card_data())

    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=event)
    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=event)  # replay

    # Scoped to this test's rows: the admin-gate session fixture commits
    # rows outside the test transaction (reconcile-test precedent).
    card = SavedCard.objects.get(user=payment.user)  # one despite the replay
    assert card.user == payment.user
    assert card.token == "fake_card_A"
    assert card.gateway_agreement_id == "fake_agr_A"
    assert card.fingerprint == "fp_fake_card_A"  # read from the (fake) vault
    payment.refresh_from_db()
    assert payment.saved_card == card


def test_paid_event_card_payload_ignored_without_opt_in() -> None:
    payment = PaymentFactory.create()  # save_card_requested defaults False

    services.payment_apply_gateway_event(
        gateway_name=GATEWAY, event=_event(payment, saved_card=_card_data())
    )

    assert not SavedCard.objects.filter(user=payment.user).exists()
    payment.refresh_from_db()
    assert payment.saved_card is None
    assert payment.status == PaymentStatus.PAID  # the payment itself applied


def test_initiate_with_saved_card_charges_instantly() -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user)

    payment = _initiate(user, saved_card=card)

    assert payment.status == PaymentStatus.PAID  # FakeGateway captures instantly
    assert payment.checkout_url == ""
    assert payment.saved_card == card
    assert payment.gateway_transaction_id == f"fake_txn_{payment.idempotency_key}"
    user.wallet.refresh_from_db()  # factory cached the pre-credit instance
    assert user.wallet.balance == Decimal("50.00")


def test_initiate_with_another_users_card_is_rejected() -> None:
    user = _payer()
    other_card = SavedCardFactory.create()

    with pytest.raises(SavedCardNotFoundError):
        _initiate(user, saved_card=other_card)

    assert user.payments.count() == 0  # rejected before any row/provider call


def test_initiate_with_gateway_mismatch_card_is_rejected() -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user, gateway=GatewayName.PAYMOB)  # SAR -> tap

    with pytest.raises(SavedCardGatewayMismatchError):
        _initiate(user, saved_card=card)


def test_charge_saved_marks_paid_and_credits_wallet() -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user)

    payment = _charge_saved(user, card)

    assert payment.status == PaymentStatus.PAID
    assert payment.saved_card == card
    assert payment.gateway_transaction_id.startswith("fake_txn_")
    user.wallet.refresh_from_db()  # factory cached the pre-credit instance
    assert user.wallet.balance == Decimal("25.00")


def test_charge_saved_declined_marks_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user)

    def declined(self: FakeGateway, *, request: CheckoutRequest) -> CheckoutSession:
        return CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url="",
            raw={"fake": True},
            outcome=PaymentEvent(
                reference=request.reference,
                transaction_id=f"fake_txn_{request.reference}",
                is_paid=False,
                is_pending=False,
                status="DECLINED",
                amount_minor=2500,
                currency="SAR",
                saved_card=None,
                raw={"fake": True},
            ),
        )

    monkeypatch.setattr(FakeGateway, "charge_saved", declined)

    payment = _charge_saved(user, card)

    assert payment.status == PaymentStatus.FAILED  # non-terminal: webhook can heal


def test_charge_saved_pending_leaves_the_row_to_the_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user)
    monkeypatch.setattr(
        FakeGateway,
        "charge_saved",
        lambda self, *, request: CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url="",
            raw={"fake": True},
            outcome=None,
        ),
    )

    payment = _charge_saved(user, card)

    assert payment.status == PaymentStatus.PENDING


def test_charge_saved_gateway_down_marks_failed_and_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _payer()
    card = SavedCardFactory.create(user=user)

    def down(self: FakeGateway, *, request: Any) -> CheckoutSession:
        raise OutboundTransportError(
            service="tap", detail="connect timeout", request_sent=False
        )

    monkeypatch.setattr(FakeGateway, "charge_saved", down)

    with pytest.raises(PaymentGatewayUnavailableError):
        _charge_saved(user, card)

    payment = user.payments.get()
    assert payment.status == PaymentStatus.FAILED


def test_saved_card_store_is_idempotent_and_reassigns_owner() -> None:
    first_owner = UserFactory.create()
    second_owner = UserFactory.create()

    services.saved_card_store(user=first_owner, gateway=GATEWAY, data=_card_data())
    card = services.saved_card_store(
        user=second_owner, gateway=GATEWAY, data=_card_data(last4="2222")
    )

    cards = SavedCard.objects.filter(gateway=GATEWAY, token="fake_card_A")
    assert cards.count() == 1  # (gateway, token) upsert, no dupes
    assert card.user == second_owner  # 3DS on the token proves possession
    assert card.last4 == "2222"  # metadata refresh rides along


def test_saved_card_store_folds_a_revaulted_card_into_the_existing_row(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """Tap mints a new card id per customer; the fingerprint is the same
    physical card. One row, repointed at the newest ids, old card detached."""
    user = UserFactory.create()
    detached: list[Any] = []

    def record_delete(self: FakeGateway, *, saved_card: Any) -> None:
        detached.append(saved_card)

    monkeypatch.setattr(FakeGateway, "delete_saved_card", record_delete)
    monkeypatch.setattr(
        FakeGateway, "saved_card_fingerprint", lambda self, *, saved_card: "fp_same"
    )
    first = services.saved_card_store(user=user, gateway=GATEWAY, data=_card_data())

    with django_capture_on_commit_callbacks(execute=True):
        second = services.saved_card_store(
            user=user,
            gateway=GATEWAY,
            data=_card_data(
                token="fake_card_B",
                customer_id="fake_cus_B",
                agreement_id="fake_agr_B",
            ),
        )

    assert second.pk == first.pk
    assert SavedCard.objects.filter(user=user, gateway=GATEWAY).count() == 1
    second.refresh_from_db()
    assert second.token == "fake_card_B"
    assert second.gateway_customer_id == "fake_cus_B"
    assert second.gateway_agreement_id == "fake_agr_B"
    assert [ref.token for ref in detached] == ["fake_card_A"]
    assert detached[0].customer_id == "fake_cus_A"


def test_saved_card_store_survives_a_failed_detach_of_the_old_card(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create()

    def failing_delete(self: FakeGateway, *, saved_card: Any) -> None:
        raise OutboundTransportError(
            service="tap", detail="read timeout", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, "delete_saved_card", failing_delete)
    monkeypatch.setattr(
        FakeGateway, "saved_card_fingerprint", lambda self, *, saved_card: "fp_same"
    )
    services.saved_card_store(user=user, gateway=GATEWAY, data=_card_data())

    with django_capture_on_commit_callbacks(execute=True):
        services.saved_card_store(
            user=user, gateway=GATEWAY, data=_card_data(token="fake_card_B")
        )

    card = SavedCard.objects.get(user=user, gateway=GATEWAY)
    assert card.token == "fake_card_B"


def test_saved_card_store_keeps_different_cards_apart() -> None:
    """The fake vault answers one fingerprint per token - two cards."""
    user = UserFactory.create()

    services.saved_card_store(user=user, gateway=GATEWAY, data=_card_data())
    services.saved_card_store(
        user=user, gateway=GATEWAY, data=_card_data(token="fake_card_B")
    )

    cards = SavedCard.objects.filter(user=user, gateway=GATEWAY)
    assert cards.count() == 2
    assert {card.fingerprint for card in cards} == {
        "fp_fake_card_A",
        "fp_fake_card_B",
    }


def test_saved_card_store_same_card_on_another_user_is_their_own_row(
    monkeypatch: pytest.MonkeyPatch,
    django_capture_on_commit_callbacks: Any,
) -> None:
    """A shared family card: one row per user, nothing detached."""
    detached: list[Any] = []

    def record_delete(self: FakeGateway, *, saved_card: Any) -> None:
        detached.append(saved_card)

    monkeypatch.setattr(FakeGateway, "delete_saved_card", record_delete)
    monkeypatch.setattr(
        FakeGateway, "saved_card_fingerprint", lambda self, *, saved_card: "fp_same"
    )
    first_owner = UserFactory.create()
    second_owner = UserFactory.create()

    with django_capture_on_commit_callbacks(execute=True):
        services.saved_card_store(user=first_owner, gateway=GATEWAY, data=_card_data())
        services.saved_card_store(
            user=second_owner, gateway=GATEWAY, data=_card_data(token="fake_card_B")
        )

    assert SavedCard.objects.filter(user=first_owner, gateway=GATEWAY).count() == 1
    assert SavedCard.objects.filter(user=second_owner, gateway=GATEWAY).count() == 1
    assert detached == []


def test_saved_card_store_reads_the_fingerprint_from_the_vault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decided by gateway capability: the payload carries no fingerprint,
    the vault is asked - once per store, for the token being stored."""
    asked: list[Any] = []

    def fingerprint(self: FakeGateway, *, saved_card: Any) -> str:
        asked.append(saved_card)
        return "fp_fetched"

    monkeypatch.setattr(FakeGateway, "saved_card_fingerprint", fingerprint)

    card = services.saved_card_store(
        user=UserFactory.create(), gateway=GATEWAY, data=_card_data()
    )

    assert card.fingerprint == "fp_fetched"
    assert [ref.token for ref in asked] == ["fake_card_A"]
    assert asked[0].customer_id == "fake_cus_A"


@pytest.mark.usefixtures("vaultless_gateway")
def test_saved_card_store_without_a_vault_api_stores_a_blank_fingerprint() -> None:
    """A gateway without a vault API (Paymob) has no fingerprint concept:
    rows stay blank and never collide on it."""
    user = UserFactory.create()

    services.saved_card_store(user=user, gateway=GATEWAY, data=_card_data())
    services.saved_card_store(
        user=user, gateway=GATEWAY, data=_card_data(token="fake_card_B")
    )

    cards = SavedCard.objects.filter(user=user, gateway=GATEWAY)
    assert cards.count() == 2
    assert {card.fingerprint for card in cards} == {""}


def test_saved_card_store_fails_when_the_vault_lookup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No blank-fingerprint fallback: the settlement carrying the card
    fails loudly and the gateway/sweep retries it."""

    def failing(self: FakeGateway, *, saved_card: Any) -> str:
        raise OutboundTransportError(
            service="tap", detail="read timeout", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, "saved_card_fingerprint", failing)
    user = UserFactory.create()

    with pytest.raises(OutboundTransportError):
        services.saved_card_store(user=user, gateway=GATEWAY, data=_card_data())

    assert not SavedCard.objects.filter(user=user).exists()


def test_initiate_files_a_new_card_under_the_users_existing_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _payer()
    SavedCardFactory.create(user=user, gateway_customer_id="fake_cus_existing")
    seen: list[CheckoutRequest] = []
    original = FakeGateway.create_checkout

    def capture(self: FakeGateway, *, request: CheckoutRequest) -> CheckoutSession:
        seen.append(request)
        return original(self, request=request)

    monkeypatch.setattr(FakeGateway, "create_checkout", capture)

    _initiate(user)

    assert seen[0].customer_id == "fake_cus_existing"


def test_initiate_without_cards_lets_the_gateway_create_the_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[CheckoutRequest] = []
    original = FakeGateway.create_checkout

    def capture(self: FakeGateway, *, request: CheckoutRequest) -> CheckoutSession:
        seen.append(request)
        return original(self, request=request)

    monkeypatch.setattr(FakeGateway, "create_checkout", capture)

    _initiate(_payer())

    assert seen[0].customer_id == ""


def test_saved_card_store_from_event_links_user_by_email() -> None:
    user = UserFactory.create()
    event = CardTokenEvent(
        saved_card=_card_data(email=user.email.upper()),  # case-insensitive
        raw={},
    )

    card = services.saved_card_store_from_event(gateway_name=GATEWAY, event=event)

    assert card is not None
    assert card.user == user


def test_saved_card_store_from_event_unknown_email_returns_none() -> None:
    event = CardTokenEvent(
        saved_card=_card_data(email="nobody@nowhere.example"), raw={}
    )

    stored = services.saved_card_store_from_event(gateway_name=GATEWAY, event=event)

    assert stored is None
    assert not SavedCard.objects.filter(token="fake_card_A").exists()


def test_saved_card_delete_removes_row_and_detaches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detached: list[Any] = []
    monkeypatch.setattr(
        FakeGateway,
        "delete_saved_card",
        lambda self, *, saved_card: detached.append(saved_card),
    )
    card = SavedCardFactory.create()

    services.saved_card_delete(user=card.user, saved_card=card)

    assert not SavedCard.objects.filter(pk=card.pk).exists()
    assert [ref.token for ref in detached] == [card.token]


def test_saved_card_delete_survives_gateway_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's intent wins: a dangling provider-side card is inert."""
    card = SavedCardFactory.create()

    def failing_delete(self: FakeGateway, *, saved_card: Any) -> None:
        raise OutboundTransportError(
            service="tap", detail="read timeout", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, "delete_saved_card", failing_delete)

    services.saved_card_delete(user=card.user, saved_card=card)

    assert not SavedCard.objects.filter(pk=card.pk).exists()


@pytest.mark.usefixtures("vaultless_gateway")
def test_saved_card_delete_without_a_vault_api_only_drops_the_row() -> None:
    """Paymob has no detach call: no lie about having detached, the row goes."""
    card = SavedCardFactory.create()

    services.saved_card_delete(user=card.user, saved_card=card)

    assert not SavedCard.objects.filter(pk=card.pk).exists()


def test_saved_card_delete_rejects_other_owner() -> None:
    card = SavedCardFactory.create()
    stranger = UserFactory.create()

    with pytest.raises(SavedCardNotFoundError):
        services.saved_card_delete(user=stranger, saved_card=card)

    assert SavedCard.objects.filter(pk=card.pk).exists()


def test_verify_persists_saved_card_from_charge_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The webhook was lost - verify still vaults the opted-in card."""
    payment = PaymentFactory.create(save_card_requested=True)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_status",
        lambda self, **kwargs: _event(
            payment, transaction_id="txn_9", saved_card=_card_data()
        ),
    )

    services.payment_verify(payment=payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.saved_card is not None
    assert payment.saved_card.token == "fake_card_A"


def test_verify_with_no_transaction_yet_leaves_the_row_pending() -> None:
    payment = PaymentFactory.create()

    assert services.payment_verify(payment=payment).status == PaymentStatus.PENDING


# --- gateway events: signed-amount cross-check, informational events, expiry ----


def test_event_with_matching_signed_amount_applies() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)  # 50.00 SAR

    services.payment_apply_gateway_event(
        gateway_name=GATEWAY, event=_event(payment, amount_minor=5000, currency="SAR")
    )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount


@pytest.mark.parametrize(("amount_minor", "currency"), [(4999, "SAR"), (5000, "EGP")])
def test_event_with_mismatched_amount_or_currency_is_never_applied(
    amount_minor: int, currency: str
) -> None:
    """The signature proves the gateway sent it; the cross-check proves it is
    about THIS payment at THIS price. Nothing is written on a mismatch."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY,
            event=_event(payment, amount_minor=amount_minor, currency=currency),
        )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    assert payment.gateway_callback is None
    assert payment.user.wallet.balance == Decimal(0)


def test_replayed_event_with_a_different_amount_is_refused() -> None:
    """Even a replay on a settled row is checked - no gateway ever gets to
    skip the amount check by omitting the fields."""
    payment = _paid_topup()

    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY, event=_event(payment, amount_minor=1)
        )


def test_pending_event_is_recorded_but_never_transitions() -> None:
    """Customer on the bank's OTP page: the row stays PENDING (not FAILED),
    the callback is kept for audit, the wallet is untouched."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)

    services.payment_apply_gateway_event(
        gateway_name=GATEWAY,
        event=_event(
            payment, is_paid=False, is_pending=True, status="pending", raw={"otp": 1}
        ),
    )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    assert payment.gateway_callback == {"otp": 1}
    assert payment.gateway_transaction_id == "txn_1"
    assert payment.user.wallet.balance == Decimal(0)


def test_child_action_event_keeps_the_settled_transaction_id() -> None:
    """A refund/void child callback on a PAID row: recorded, PAID kept, the
    settled id (what OUR refund targets) never replaced, wallet never
    auto-debited - a human reconciles the provider-side action. Its amount
    is the child's (a partial refund), so the cross-check does not apply."""
    payment = _paid_topup()
    assert payment.gateway_transaction_id == "txn_1"

    services.payment_apply_gateway_event(
        gateway_name=GATEWAY,
        event=_event(
            payment,
            transaction_id="",
            is_paid=False,
            is_pending=True,
            status="refund",
            amount_minor=2000,
            raw={"child": True},
        ),
    )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.gateway_transaction_id == "txn_1"
    assert payment.gateway_callback == {"child": True}
    assert payment.user.wallet.balance == payment.amount


def test_verify_cross_checks_the_provider_amount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_status",
        lambda self, **kwargs: _event(
            payment, transaction_id="txn_9", amount_minor=100
        ),
    )

    with pytest.raises(PaymentEventMismatchError):
        services.payment_verify(payment=payment)

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING


def _backdate(payment: Payment, *, hours: int) -> None:
    stamp = timezone.now() - timedelta(hours=hours)
    Payment.objects.filter(pk=payment.pk).update(created_at=stamp, updated_at=stamp)


def test_expire_fails_an_abandoned_checkout() -> None:
    payment = PaymentFactory.create()
    _backdate(payment, hours=3)

    assert services.payment_expire(payment=payment).status == PaymentStatus.FAILED


@pytest.mark.parametrize("hours", [0, 1])
def test_expire_leaves_a_checkout_the_customer_can_still_complete(hours: int) -> None:
    payment = PaymentFactory.create()
    _backdate(payment, hours=hours)

    assert services.payment_expire(payment=payment).status == PaymentStatus.PENDING


def test_expire_never_touches_a_paid_row() -> None:
    payment = _paid_topup()
    _backdate(payment, hours=3)

    assert services.payment_expire(payment=payment).status == PaymentStatus.PAID


def test_late_webhook_heals_an_expired_row() -> None:
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    _backdate(payment, hours=3)
    services.payment_expire(payment=payment)

    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=_event(payment))

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount


def test_reconcile_expires_abandoned_pending_rows() -> None:
    """Abandoned checkouts must leave PENDING, or the sweep's oldest-first
    window fills up with them and newer stale rows are never re-checked.
    The fake vault reports no transaction yet (None) for every reference."""
    abandoned = PaymentFactory.create()
    _backdate(abandoned, hours=3)
    stale_but_live = PaymentFactory.create()
    _age(stale_but_live, minutes=60)  # updated_at only - created just now

    call_command("reconcile_payments")

    abandoned.refresh_from_db()
    stale_but_live.refresh_from_db()
    assert abandoned.status == PaymentStatus.FAILED
    assert stale_but_live.status == PaymentStatus.PENDING


# --- charge_saved_card (ops command) ----------------------------------------------


def test_charge_saved_card_command_requires_kind_and_description() -> None:
    card = SavedCardFactory.create(user=_payer())

    with pytest.raises(CommandError, match="--kind"):
        call_command("charge_saved_card", str(card.pk), "25.00", "SAR")


def test_charge_saved_card_command_drives_the_service() -> None:
    card = SavedCardFactory.create(user=_payer())

    call_command(
        "charge_saved_card",
        str(card.pk),
        "25.00",
        "SAR",
        kind="wallet_topup",
        description="ops top-up",
    )

    payment = card.user.payments.get()
    assert payment.status == PaymentStatus.PAID
    assert payment.description == "ops top-up"


def test_charge_saved_card_command_rejects_an_unknown_kind() -> None:
    card = SavedCardFactory.create(user=_payer())

    with pytest.raises(CommandError, match="not a valid PaymentKind"):
        call_command(
            "charge_saved_card",
            str(card.pk),
            "25.00",
            "SAR",
            kind="subscription",
            description="x",
        )

    assert not card.user.payments.exists()
