"""Nothing runs on a timer: stuck payments must be VISIBLE (sidebar badge,
"Needs attention" filter) and recoverable by hand ("Verify with provider")."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.payments import selectors
from apps.payments import services
from apps.payments.admin.badges import payments_needing_attention
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.gateways.base import PaymentEvent
from apps.payments.models import Payment
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.fake_gateway import FakeGateway
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CONFIRM = {"_form_submitted": "1"}


def _aged(payment: Payment, **kwargs: Any) -> Payment:
    Payment.objects.filter(pk=payment.pk).update(
        created_at=timezone.now() - timedelta(hours=3), **kwargs
    )
    payment.refresh_from_db()
    return payment


def test_attention_lists_stale_pending_and_attempted_refunds() -> None:
    fresh = PaymentFactory.create()
    stale = _aged(PaymentFactory.create())
    attempted = PaymentFactory.create(
        status=PaymentStatus.REFUND_PENDING, refund_attempted_at=timezone.now()
    )
    interlocked = PaymentFactory.create(status=PaymentStatus.REFUND_PENDING)
    paid = _aged(PaymentFactory.create(), status=PaymentStatus.PAID)

    flagged = set(selectors.payments_needing_attention())

    assert {stale, attempted} <= flagged
    assert not {fresh, interlocked, paid} & flagged


def test_badge_counts_for_a_viewer_and_hides_when_clean(rf: Any) -> None:
    staff = UserFactory.create(is_staff=True, is_superuser=True)
    request = rf.get("/admin/")
    request.user = staff
    baseline = selectors.payments_needing_attention().count()
    _aged(PaymentFactory.create())

    assert payments_needing_attention(request) == str(baseline + 1)
    request.user = UserFactory.create(is_staff=True)  # no payments permission
    assert payments_needing_attention(request) == ""


def test_needs_attention_filter_narrows_the_changelist(client: Client) -> None:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    stale = _aged(PaymentFactory.create())
    PaymentFactory.create()

    response = client.get(
        reverse("admin:payments_payment_changelist"), {"attention": "yes"}
    )

    assert response.status_code == 200
    assert set(response.context["cl"].queryset) == set(
        selectors.payments_needing_attention()
    )
    assert stale in response.context["cl"].queryset


def test_verify_action_settles_a_pending_row_from_the_provider(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    monkeypatch.setattr(
        FakeGateway,
        "fetch_status",
        lambda self, **kwargs: PaymentEvent(
            reference=str(payment.idempotency_key),
            charge_id=payment.gateway_charge_id,
            transaction_id="txn_verified",
            is_paid=True,
            is_pending=False,
            status="CAPTURED",
            amount_minor=int(payment.amount * 100),
            currency=str(payment.currency),
            saved_card=None,
            raw={},
        ),
    )
    url = reverse("admin:payments_payment_verify_payment", args=[payment.pk])

    assert client.get(url).status_code == 200  # the dialog, no side effect
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PENDING
    response = client.post(url, CONFIRM)

    assert response.status_code == 302
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    assert payment.user.wallet.balance == payment.amount


def test_verify_action_is_offered_only_for_pending_rows(client: Client) -> None:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    payment = PaymentFactory.create(status=PaymentStatus.PAID)

    response = client.post(
        reverse("admin:payments_payment_verify_payment", args=[payment.pk]), CONFIRM
    )

    assert response.status_code == 403


def test_unknown_checkout_outcome_is_logged_as_an_error(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no sweep, a lost provider response must page (ERROR -> Sentry)."""
    import uuid

    from apps.common.http import OutboundTransportError
    from apps.payments.constants import Currency
    from apps.payments.exceptions import PaymentGatewayUnavailableError

    def read_timeout(self: FakeGateway, *, request: Any) -> Any:
        raise OutboundTransportError(
            service="tap", detail="read timeout", request_sent=True
        )

    monkeypatch.setattr(FakeGateway, "create_checkout", read_timeout)
    user = UserFactory.create(phone="+966501234567")

    with caplog.at_level("ERROR"), pytest.raises(PaymentGatewayUnavailableError):
        services.payment_initiate(
            user=user,
            request_id=uuid.uuid4(),
            amount=payment_amount(),
            currency=Currency.SAR,
            kind=PaymentKind.WALLET_TOPUP,
            description="x",
            saved_card=None,
        )

    assert any(
        "payment_checkout_outcome_unknown" in record.getMessage()
        for record in caplog.records
        if record.levelname == "ERROR"
    )


def payment_amount() -> Any:
    from decimal import Decimal

    return Decimal("50.00")
