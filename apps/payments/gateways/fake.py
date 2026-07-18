"""The test gateway - Mailpit's role for payments (test.py maps every
currency here so suites never touch provider HTTP).

Checkout "succeeds" instantly with a fake URL; the webhook path is exercised
with ``manage.py simulate_payment_webhook <payment-pk> [--fail]``, which
drives the SAME transition service the real webhook endpoint calls.
"""

import json
from collections.abc import Mapping
from decimal import Decimal

from django.conf import settings

from apps.payments.gateways.base import ChargeStatus
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.base import WebhookVerificationError

_SIGNATURE_HEADER = "x-fake-signature"
_SIGNATURE = "fake-signature"


class FakeGateway:
    name = "fake"

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        return CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url=(
                f"{settings.FRONTEND_BASE_URL}/fake-checkout/{request.reference}"
            ),
            raw={"fake": True},
        )

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> WebhookEvent:
        # Even the fake verifies - the endpoint's 400 path stays testable.
        if headers.get(_SIGNATURE_HEADER, "") != _SIGNATURE:
            msg = f"missing/invalid {_SIGNATURE_HEADER} header"
            raise WebhookVerificationError(msg)
        payload = json.loads(body)
        return WebhookEvent(
            reference=str(payload.get("reference", "")),
            transaction_id=str(payload.get("transaction_id", "fake_txn")),
            is_paid=bool(payload.get("paid", False)),
            status="PAID" if payload.get("paid") else "FAILED",
            raw=payload,
        )

    def fetch_status(self, *, charge_id: str, reference: str) -> ChargeStatus:
        return ChargeStatus(
            transaction_id="", is_paid=False, status="pending", raw={"fake": True}
        )

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        return RefundResult(ok=True, raw={"fake": True})
