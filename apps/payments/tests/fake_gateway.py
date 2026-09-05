"""The test gateway - Mailpit's role for payments (test.py maps every
currency here so suites never touch provider HTTP).

It impersonates Tap (``name = "tap"``): ``Payment.gateway`` only admits the
real gateways, so a test double has to answer to one of their names to
produce rows that pass model validation. Checkout "succeeds" instantly with a
fake URL; the webhook path is exercised by POSTing a signed body to
``/webhooks/tap`` (``parse_webhook`` below is the one simulation road);
saved-card charges (one-click and MIT) capture instantly - the redirect-CIT
and declined paths live in the respx gateway tests.
"""

import json
from collections.abc import Mapping
from decimal import Decimal

from django.conf import settings

from apps.payments.gateways.base import CardTokenEvent
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import RefundStatus
from apps.payments.gateways.base import SavedCardData
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookVerificationError
from apps.payments.gateways.base import to_minor_units

SIGNATURE_HEADER = "x-fake-signature"
SIGNATURE = "fake-signature"


class FakeGateway:
    name = "tap"

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        if request.saved_card is not None:
            return self._instant_capture(request)
        return CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url=(
                f"{settings.FRONTEND_BASE_URL}/fake-checkout/{request.reference}"
            ),
            raw={"fake": True},
            outcome=None,
        )

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession:
        return self._instant_capture(request)

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> None:
        return

    def saved_card_fingerprint(self, *, saved_card: SavedCardRef) -> str:
        # One fingerprint per token, like a real vault would answer for
        # distinct physical cards; tests that need two tokens to be the same
        # card monkeypatch this.
        return f"fp_{saved_card.token}"

    def _instant_capture(self, request: CheckoutRequest) -> CheckoutSession:
        return CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url="",
            raw={"fake": True},
            outcome=PaymentEvent(
                reference=request.reference,
                charge_id=f"fake_charge_{request.reference}",
                transaction_id=f"fake_txn_{request.reference}",
                is_paid=True,
                is_pending=False,
                status="CAPTURED",
                amount_minor=to_minor_units(amount=request.amount),
                currency=str(request.currency),
                saved_card=None,
                raw={"fake": True},
            ),
        )

    def parse_webhook(
        self,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str],
        body: bytes,
    ) -> PaymentEvent | CardTokenEvent:
        # Even the fake verifies - the endpoint's 400 path stays testable.
        if headers.get(SIGNATURE_HEADER, "") != SIGNATURE:
            msg = f"missing/invalid {SIGNATURE_HEADER} header"
            raise WebhookVerificationError(msg)
        payload = json.loads(body)
        if "card_token" in payload:  # standalone token event (Paymob TOKEN shape)
            return CardTokenEvent(
                saved_card=SavedCardData(
                    token=str(payload["card_token"]),
                    customer_id="",
                    agreement_id="",
                    brand="VISA",
                    last4="4242",
                    exp_month=None,
                    exp_year=None,
                    email=str(payload["email"]),
                ),
                raw=payload,
            )
        reference = str(payload["reference"])
        saved_card = None
        if payload.get("save_card"):  # payment event that also vaulted a card
            saved_card = SavedCardData(
                token=f"fake_card_{reference}",
                customer_id=f"fake_cus_{reference}",
                agreement_id=f"fake_agr_{reference}",
                brand="VISA",
                last4="4242",
                exp_month=12,
                exp_year=2030,
                email="",
            )
        paid = bool(payload["paid"])
        return PaymentEvent(
            reference=reference,
            # The identity a real gateway signs; the simulated body may name
            # another checkout's to exercise the binding check.
            charge_id=str(payload.get("charge_id", f"fake_charge_{reference}")),
            transaction_id=str(payload.get("transaction_id", "fake_txn")),
            is_paid=paid,
            is_pending=False,
            status="PAID" if paid else "FAILED",
            # A real gateway signs these; the simulated body states them.
            amount_minor=int(payload["amount_minor"]),
            currency=str(payload["currency"]),
            saved_card=saved_card,
            raw=payload,
        )

    def fetch_status(self, *, charge_id: str, reference: str) -> PaymentEvent | None:
        return None  # nothing settled yet; tests monkeypatch an outcome

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        return RefundResult(
            status=RefundStatus.SUCCEEDED,
            refund_id=f"fake_refund_{transaction_id}",
            raw={"fake": True},
        )
