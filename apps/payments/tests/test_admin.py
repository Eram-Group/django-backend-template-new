"""The refund admin action: interlock in the request, executor on commit."""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from apps.payments import services
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.gateways.base import WebhookEvent
from apps.payments.tests.factories import PaymentFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_refund_action_runs_interlock_and_executor(
    client: Client,
    django_capture_on_commit_callbacks: Any,
) -> None:
    superuser = UserFactory.create(is_staff=True, is_superuser=True)
    client.force_login(superuser)
    payment = PaymentFactory.create(kind=PaymentKind.WALLET_TOPUP)
    services.payment_apply_gateway_event(
        gateway_name="fake",
        event=WebhookEvent(
            reference=str(payment.idempotency_key),
            transaction_id="txn_1",
            is_paid=True,
            status="PAID",
            raw={},
        ),
    )
    url = reverse("admin:payments_payment_refund_payment", args=[payment.pk])

    with django_capture_on_commit_callbacks(execute=True):
        response = client.get(url)

    assert response.status_code == 302
    assert response["Location"] == reverse(
        "admin:payments_payment_change", args=[payment.pk]
    )
    assert any("Refund started" in str(m) for m in get_messages(response.wsgi_request))
    payment.refresh_from_db()
    assert payment.status == PaymentStatus.REFUNDED  # ImmediateBackend ran inline
    wallet = payment.user.wallet
    wallet.refresh_from_db()
    assert wallet.balance == Decimal(0)
