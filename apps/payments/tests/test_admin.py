"""The refund admin action: interlock in the request, executor on commit."""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from django.utils.translation import gettext

from apps.payments import services
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.gateways.base import PaymentEvent
from apps.payments.tests.factories import PaymentFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

#: What unfold's confirmation dialog posts when the operator confirms.
CONFIRM = {"_form_submitted": "1"}


def test_refund_action_runs_interlock_and_executor(
    client: Client,
    run_enqueued_tasks: Any,
) -> None:
    superuser = UserFactory.create(is_staff=True, is_superuser=True)
    client.force_login(superuser)
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name=GatewayName.TAP,  # the test FakeGateway answers to it
        event=PaymentEvent(
            reference=str(payment.idempotency_key),
            charge_id=payment.gateway_charge_id,
            transaction_id="txn_1",
            is_paid=True,
            is_pending=False,
            status="PAID",
            amount_minor=int(payment.amount * 100),
            currency=str(payment.currency),
            saved_card=None,
            raw={},
        ),
    )
    url = reverse("admin:payments_payment_refund_payment", args=[payment.pk])

    with run_enqueued_tasks():
        response = client.post(url, CONFIRM)

    assert response.status_code == 302
    assert response["Location"] == reverse(
        "admin:payments_payment_change", args=[payment.pk]
    )
    started = gettext("Refund started - refresh to see the final status.")
    assert any(started in str(m) for m in get_messages(response.wsgi_request))
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED  # the drained executor task
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)


def test_refund_get_only_renders_the_confirmation() -> None:
    """A GET on the action URL (a prefetch, an unfurl, a history restore)
    must not move money - the dialog is the only road to the POST."""
    admin = UserFactory.create(is_staff=True, is_superuser=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(admin)
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name=GatewayName.TAP,
        event=PaymentEvent(
            reference=str(payment.idempotency_key),
            charge_id=payment.gateway_charge_id,
            transaction_id="txn_1",
            is_paid=True,
            is_pending=False,
            status="PAID",
            amount_minor=int(payment.amount * 100),
            currency=str(payment.currency),
            saved_card=None,
            raw={},
        ),
    )
    url = reverse("admin:payments_payment_refund_payment", args=[payment.pk])

    response = client.get(url)

    assert response.status_code == 200
    assert 'name="_form_submitted"' in response.content.decode()
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
    # And a POST without the CSRF token is refused too.
    assert client.post(url, CONFIRM).status_code == 403
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.PAID
