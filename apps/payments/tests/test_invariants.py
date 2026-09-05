"""The money invariants from the 2026-09-05 review, pinned.

Every case here reproduced a real defect: a stale checkout write undoing a
webhook's PAID (double credit on replay), a valid provider signature
re-addressed to another checkout, a retried POST opening a second checkout,
and a provider "accepted" refund finalized as done. Gateways are mocked;
nothing here reaches a provider.
"""

import json
import uuid
from datetime import timedelta
from decimal import Decimal
from functools import partial
from typing import Any

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone
from pydantic import SecretStr

from apps.common.http import OutboundTransportError
from apps.payments import services
from apps.payments.constants import Currency
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import PaymentEventMismatchError
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentRefundFailedError
from apps.payments.exceptions import PaymentRequestConflictError
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import RefundStatus
from apps.payments.gateways.paymob import PaymobGateway
from apps.payments.gateways.tap import TapGateway
from apps.payments.models import Payment
from apps.payments.models import WalletTransaction
from apps.payments.tests import test_gateway_paymob as paymob_fixtures
from apps.payments.tests import test_gateway_tap as tap_fixtures
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.factories import SavedCardFactory
from apps.payments.tests.fake_gateway import FakeGateway
from apps.users.models import User
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

GATEWAY = GatewayName.TAP  # the FakeGateway answers to it
PHONE = "+966501234567"


def _payer() -> User:
    return UserFactory.create(phone=PHONE)


def _event(payment: Payment, **overrides: Any) -> PaymentEvent:
    fields: dict[str, Any] = {
        "reference": str(payment.idempotency_key),
        "charge_id": payment.gateway_charge_id,
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


def _topup_kwargs(user: User) -> dict[str, Any]:
    return {
        "user": user,
        "amount": Decimal("50.00"),
        "currency": Currency.SAR,
        "kind": PaymentKind.WALLET_TOPUP,
        "description": "Review",
    }


# --- 1. a lost checkout response never undoes a webhook -------------------------


@pytest.mark.parametrize("method", ["create_checkout", "charge_saved"])
def test_paid_webhook_then_checkout_timeout_cannot_double_credit(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """The provider captured and its webhook settled the row while our
    request was still waiting for the response; the response then never
    arrives. The stale request must not mark the row FAILED (a replayed
    webhook would re-credit), and the wallet is credited exactly once."""
    user = _payer()
    events: list[PaymentEvent] = []

    def webhook_then_timeout(self: FakeGateway, *, request: CheckoutRequest) -> Any:
        payment = Payment.objects.get(idempotency_key=request.reference)
        # The webhook lands first - it carries the identity the response
        # would have carried (the row learns it from the provider's answer).
        charge_id = f"fake_charge_{request.reference}"
        monkeypatch.setattr(
            FakeGateway,
            "fetch_status",
            lambda self, **kwargs: _event(
                payment, charge_id=charge_id, transaction_id="txn_captured"
            ),
        )
        event = _event(payment, charge_id=charge_id, transaction_id="txn_captured")
        events.append(event)
        services.payment_apply_gateway_event(gateway_name=GATEWAY, event=event)
        raise OutboundTransportError(
            service="tap", detail="response lost after capture", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, method, webhook_then_timeout)
    kwargs: dict[str, Any] = _topup_kwargs(user)
    if method == "create_checkout":
        checkout = partial(
            services.payment_initiate,
            **kwargs,
            request_id=uuid.uuid4(),
            saved_card=None,
        )
    else:
        checkout = partial(
            services.payment_charge_saved,
            **kwargs,
            saved_card=SavedCardFactory.create(user=user),
        )
    with pytest.raises(PaymentGatewayUnavailableError):
        checkout()

    payment = user.payments.get()
    assert payment.status == PaymentStatus.PAID  # the settlement survived
    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=events[0])
    user.wallet.refresh_from_db()
    assert user.wallet.balance == Decimal("50.00")
    assert WalletTransaction.objects.filter(wallet=user.wallet).count() == 1


def test_unknown_checkout_outcome_leaves_the_row_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A read timeout may mean the provider opened the session: the row
    stays PENDING for the webhook / reconcile sweep, never FAILED."""

    def read_timeout(self: FakeGateway, *, request: CheckoutRequest) -> Any:
        raise OutboundTransportError(
            service="tap", detail="read timeout", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, "create_checkout", read_timeout)
    user = _payer()

    with pytest.raises(PaymentGatewayUnavailableError):
        services.payment_initiate(
            **_topup_kwargs(user), request_id=uuid.uuid4(), saved_card=None
        )

    assert user.payments.get().status == PaymentStatus.PENDING


def test_topup_ledger_is_unique_per_payment() -> None:
    """The DB backstop: a second TOPUP row for one payment cannot exist."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=_event(payment))
    wallet = payment.user.wallet

    with pytest.raises(IntegrityError), transaction.atomic():
        WalletTransaction.objects.create(
            wallet=wallet,
            kind=WalletTransactionKind.TOPUP,
            amount=payment.amount,
            balance_after=wallet.balance + payment.amount,
            payment=payment,
        )


# --- 2. a signed event is bound to the row it names -----------------------------


def test_event_naming_another_checkouts_identity_is_refused() -> None:
    """Same user, same price, valid signature - but the signed charge id is
    another checkout's: the unsigned reference alone must not settle."""
    original = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    other = PaymentFactory.create(user=original.user, kind=PaymentKind.WALLET_TOPUP)

    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY,
            event=_event(other, charge_id=original.gateway_charge_id),
        )

    other.refresh_from_db()
    assert other.status == PaymentStatus.PENDING


def test_tap_signed_capture_cannot_be_rebound_to_another_checkout(
    settings: Any,
) -> None:
    settings.TAP_SECRET_KEY = SecretStr(tap_fixtures.SECRET)
    user = UserFactory.create()
    payload = tap_fixtures._webhook_payload()
    original = PaymentFactory.create(
        user=user, amount=Decimal("50.00"), gateway_charge_id=payload["id"]
    )
    other = PaymentFactory.create(
        user=user, amount=Decimal("50.00"), gateway_charge_id="chg_other"
    )
    payload["reference"]["transaction"] = str(original.idempotency_key)
    signature = tap_fixtures._sign(payload)
    gateway = TapGateway()

    def parse() -> PaymentEvent:
        event = gateway.parse_webhook(
            headers={"hashstring": signature},
            params={},
            body=json.dumps(payload).encode(),
        )
        assert isinstance(event, PaymentEvent)
        return event

    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=parse())
    # The same valid receipt, re-addressed: reference.transaction is not
    # in Tap's hashstring, so the signature still verifies.
    payload["reference"]["transaction"] = str(other.idempotency_key)
    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(gateway_name=GATEWAY, event=parse())

    original.refresh_from_db()
    other.refresh_from_db()
    assert original.status == PaymentStatus.PAID
    assert other.status == PaymentStatus.PENDING


def test_paymob_signed_capture_cannot_be_rebound_to_another_checkout(
    settings: Any,
) -> None:
    settings.PAYMOB_SECRET_KEY = SecretStr(paymob_fixtures.SECRET)
    settings.PAYMOB_PUBLIC_KEY = "pk_test_paymob"
    settings.PAYMOB_HMAC_SECRET = SecretStr(paymob_fixtures.HMAC_SECRET)
    settings.PAYMOB_API_KEY = SecretStr(paymob_fixtures.API_KEY)
    settings.PAYMOB_INTEGRATION_IDS = [11]
    settings.PAYMOB_COF_INTEGRATION_ID = paymob_fixtures.COF_ID
    settings.PAYMOB_MOTO_INTEGRATION_ID = paymob_fixtures.MOTO_ID
    user = UserFactory.create()
    obj = paymob_fixtures._webhook_obj()
    original = PaymentFactory.create(
        user=user,
        amount=Decimal("75.50"),
        currency=Currency.EGP,
        gateway=GatewayName.PAYMOB,
        kind=PaymentKind.OTHER,
        gateway_charge_id=str(obj["order"]["id"]),
    )
    other = PaymentFactory.create(
        user=user,
        amount=Decimal("75.50"),
        currency=Currency.EGP,
        gateway=GatewayName.PAYMOB,
        kind=PaymentKind.OTHER,
        gateway_charge_id="9999",
    )
    obj["order"]["merchant_order_id"] = str(original.idempotency_key)
    signature = paymob_fixtures._sign(obj)
    gateway = PaymobGateway()

    def parse() -> PaymentEvent:
        event = gateway.parse_webhook(
            headers={},
            params={"hmac": signature},
            body=json.dumps({"type": "TRANSACTION", "obj": obj}).encode(),
        )
        assert isinstance(event, PaymentEvent)
        return event

    services.payment_apply_gateway_event(gateway_name=GatewayName.PAYMOB, event=parse())
    obj["order"]["merchant_order_id"] = str(other.idempotency_key)  # unsigned
    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(
            gateway_name=GatewayName.PAYMOB, event=parse()
        )

    other.refresh_from_db()
    assert other.status == PaymentStatus.PENDING


def test_lost_identity_is_bound_from_the_providers_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash between the provider call and the row update: the row has no
    charge id when the webhook arrives. The provider's own answer for that
    charge (echoing OUR reference) binds it - and the fetched event, not
    the webhook body, is what gets applied."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP, gateway_charge_id="")
    asked: list[str] = []

    def fetch_status(self: FakeGateway, *, charge_id: str, reference: str) -> Any:
        asked.append(charge_id)
        return _event(payment, charge_id="chg_real", transaction_id="txn_real")

    monkeypatch.setattr(FakeGateway, "fetch_status", fetch_status)

    services.payment_apply_gateway_event(
        gateway_name=GATEWAY,
        event=_event(payment, charge_id="chg_real", transaction_id="txn_webhook"),
    )

    assert asked == ["chg_real"]
    payment.refresh_from_db()
    assert payment.gateway_charge_id == "chg_real"
    assert payment.gateway_transaction_id == "txn_real"  # the provider's answer
    assert payment.status == PaymentStatus.PAID


def test_lost_identity_bind_refuses_a_foreign_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The provider's object does not carry our reference: whoever sent the
    webhook named a charge that is not ours."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP, gateway_charge_id="")
    monkeypatch.setattr(
        FakeGateway,
        "fetch_status",
        lambda self, **kwargs: _event(
            payment, reference=str(uuid.uuid4()), charge_id="chg_theirs"
        ),
    )

    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY, event=_event(payment, charge_id="chg_theirs")
        )

    payment.refresh_from_db()
    assert payment.gateway_charge_id == ""
    assert payment.status == PaymentStatus.PENDING


def test_settled_row_refuses_a_second_transaction() -> None:
    """One provider transaction settles one row; a later event naming a
    different transaction for the same order is refused, not recorded."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name=GATEWAY, event=_event(payment, transaction_id="txn_1")
    )

    with pytest.raises(PaymentEventMismatchError):
        services.payment_apply_gateway_event(
            gateway_name=GATEWAY, event=_event(payment, transaction_id="txn_2")
        )

    payment.refresh_from_db()
    assert payment.gateway_transaction_id == "txn_1"


def test_declined_attempt_may_be_retried_under_a_new_transaction() -> None:
    """A hosted checkout declined once and paid on the retry is one row
    with two attempts - the settling one wins."""
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name=GATEWAY,
        event=_event(payment, transaction_id="txn_declined", is_paid=False),
    )
    services.payment_apply_gateway_event(
        gateway_name=GATEWAY, event=_event(payment, transaction_id="txn_paid")
    )

    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.gateway_transaction_id == "txn_paid"


def test_one_provider_transaction_settles_one_row() -> None:
    """DB backstop for the identity rules: the settled transaction id is
    unique per gateway."""
    settled = PaymentFactory.create(gateway_transaction_id="txn_shared")

    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentFactory.create(user=settled.user, gateway_transaction_id="txn_shared")


# --- 12. a retried POST is the same operation -----------------------------------


def test_initiate_replays_the_same_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _payer()
    request_id = uuid.uuid4()
    sessions: list[str] = []
    real = FakeGateway.create_checkout

    def counting(self: FakeGateway, *, request: CheckoutRequest) -> CheckoutSession:
        sessions.append(request.reference)
        return real(self, request=request)

    monkeypatch.setattr(FakeGateway, "create_checkout", counting)

    first = services.payment_initiate(
        **_topup_kwargs(user), request_id=request_id, saved_card=None
    )
    again = services.payment_initiate(
        **_topup_kwargs(user), request_id=request_id, saved_card=None
    )

    assert again.pk == first.pk
    assert again.client_request_id == request_id
    assert len(sessions) == 1  # one provider session, one checkout
    assert user.payments.count() == 1


def test_initiate_refuses_a_reused_request_id_with_another_payload() -> None:
    user = _payer()
    request_id = uuid.uuid4()
    services.payment_initiate(
        **_topup_kwargs(user), request_id=request_id, saved_card=None
    )

    with pytest.raises(PaymentRequestConflictError) as excinfo:
        services.payment_initiate(
            **{**_topup_kwargs(user), "amount": Decimal("60.00")},
            request_id=request_id,
            saved_card=None,
        )

    assert excinfo.value.status_code == 409
    assert user.payments.count() == 1


def test_request_ids_are_scoped_per_user() -> None:
    request_id = uuid.uuid4()
    first = services.payment_initiate(
        **_topup_kwargs(_payer()), request_id=request_id, saved_card=None
    )
    second = services.payment_initiate(
        **_topup_kwargs(_payer()), request_id=request_id, saved_card=None
    )

    assert first.pk != second.pk


# --- 4. an accepted refund is not a finished refund -----------------------------


def _refund_pending(
    monkeypatch: pytest.MonkeyPatch, *, answer: RefundStatus
) -> Payment:
    """PAID top-up, refund started and executed against a provider that
    answers ``answer``."""
    staff = UserFactory.create(staff=True)
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(gateway_name=GATEWAY, event=_event(payment))
    payment.refresh_from_db()
    monkeypatch.setattr(
        FakeGateway,
        "refund",
        lambda self, **kwargs: RefundResult(
            status=answer, refund_id="re_accepted", raw={"status": str(answer)}
        ),
    )
    # The interlock enqueues the executor on commit - which never fires
    # inside the test transaction - so the executor is driven by hand.
    services.payment_refund_start(payment=payment, actor=staff)
    services.payment_refund_execute(payment_id=payment.pk, actor=staff)
    payment.refresh_from_db()
    return payment


def test_accepted_refund_stays_pending_with_the_provider_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payment = _refund_pending(monkeypatch, answer=RefundStatus.PENDING)

    assert payment.status == PaymentStatus.REFUND_PENDING
    assert payment.gateway_refund_id == "re_accepted"
    assert payment.refund_attempted_at is not None  # never re-sent
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)  # debit stands until the provider settles


@pytest.mark.parametrize(
    ("answer", "status", "balance"),
    [
        (RefundStatus.SUCCEEDED, PaymentStatus.REFUNDED, Decimal(0)),
        (RefundStatus.FAILED, PaymentStatus.PAID, Decimal("50.00")),
    ],
)
def test_refund_verify_finishes_an_accepted_refund(
    monkeypatch: pytest.MonkeyPatch,
    answer: RefundStatus,
    status: PaymentStatus,
    balance: Decimal,
) -> None:
    payment = _refund_pending(monkeypatch, answer=RefundStatus.PENDING)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_refund",
        lambda self, *, refund_id: RefundResult(
            status=answer, refund_id=refund_id, raw={"status": str(answer)}
        ),
    )

    if answer == RefundStatus.FAILED:
        with pytest.raises(PaymentRefundFailedError):
            services.payment_refund_verify(payment=payment)
    else:
        services.payment_refund_verify(payment=payment)

    payment.refresh_from_db()
    assert payment.status == status
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == balance


def test_reconcile_verifies_accepted_refunds(monkeypatch: pytest.MonkeyPatch) -> None:
    payment = _refund_pending(monkeypatch, answer=RefundStatus.PENDING)
    Payment.objects.filter(pk=payment.pk).update(
        updated_at=timezone.now() - timedelta(hours=1)
    )
    asked: list[str] = []

    def fetch_refund(self: FakeGateway, *, refund_id: str) -> RefundResult:
        asked.append(refund_id)
        return RefundResult(
            status=RefundStatus.SUCCEEDED, refund_id=refund_id, raw={"fake": True}
        )

    monkeypatch.setattr(FakeGateway, "fetch_refund", fetch_refund)

    call_command("reconcile_payments")

    assert asked == ["re_accepted"]
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED
