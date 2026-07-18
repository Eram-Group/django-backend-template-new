"""The test gateway - Mailpit's role for payments (test.py maps every
currency here so suites never touch provider HTTP).

Checkout "succeeds" instantly with a fake URL; the webhook path is exercised
with ``manage.py simulate_payment_webhook <payment-pk> [--fail] [--save-card]``,
which drives the SAME transition service the real webhook endpoint calls.
Saved-card charges (one-click and MIT) capture instantly - the redirect-CIT
and declined paths live in the respx gateway tests.
"""

import json
from collections.abc import Mapping
from decimal import Decimal

from django.conf import settings

from apps.payments.gateways.base import ChargeStatus
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import SavedCardData
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEvent
from apps.payments.gateways.base import WebhookEventKind
from apps.payments.gateways.base import WebhookVerificationError

_SIGNATURE_HEADER = "x-fake-signature"
_SIGNATURE = "fake-signature"


class FakeGateway:
    name = "fake"

    def create_checkout(self, *, request: CheckoutRequest) -> CheckoutSession:
        if request.saved_card is not None:
            return self._instant_capture(request)
        return CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url=(
                f"{settings.FRONTEND_BASE_URL}/fake-checkout/{request.reference}"
            ),
            raw={"fake": True},
        )

    def charge_saved(self, *, request: CheckoutRequest) -> CheckoutSession:
        return self._instant_capture(request)

    def setup_card(self, *, request: CheckoutRequest) -> CheckoutSession:
        # Pending like a real hosted setup; simulate_payment_webhook
        # <pk> --save-card plays the gateway callback that vaults the card.
        return CheckoutSession(
            charge_id=f"fake_setup_{request.reference}",
            checkout_url=(
                f"{settings.FRONTEND_BASE_URL}/fake-checkout/{request.reference}"
            ),
            raw={"fake": True, "card_token": request.card_token},
        )

    def delete_saved_card(self, *, saved_card: SavedCardRef) -> bool:
        return True

    def _instant_capture(self, request: CheckoutRequest) -> CheckoutSession:
        return CheckoutSession(
            charge_id=f"fake_charge_{request.reference}",
            checkout_url="",
            raw={"fake": True},
            is_paid=True,
            status="CAPTURED",
            transaction_id=f"fake_txn_{request.reference}",
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
        if "card_token" in payload:  # standalone token event (Paymob TOKEN shape)
            return WebhookEvent(
                reference="",
                transaction_id="",
                is_paid=False,
                status="token",
                raw=payload,
                kind=WebhookEventKind.CARD_TOKEN,
                saved_card=SavedCardData(
                    token=str(payload["card_token"]),
                    customer_id="",
                    agreement_id="",
                    brand=str(payload.get("brand", "VISA")),
                    last4=str(payload.get("last4", "4242")),
                    exp_month=None,
                    exp_year=None,
                    email=str(payload.get("email", "")),
                ),
            )
        reference = str(payload.get("reference", ""))
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
                email=str(payload.get("email", "")),
            )
        return WebhookEvent(
            reference=reference,
            transaction_id=str(payload.get("transaction_id", "fake_txn")),
            is_paid=bool(payload.get("paid", False)),
            status="PAID" if payload.get("paid") else "FAILED",
            raw=payload,
            saved_card=saved_card,
        )

    def fetch_status(self, *, charge_id: str, reference: str) -> ChargeStatus:
        return ChargeStatus(
            transaction_id="", is_paid=False, status="pending", raw={"fake": True}
        )

    def refund(
        self, *, transaction_id: str, amount: Decimal, currency: str
    ) -> RefundResult:
        return RefundResult(ok=True, raw={"fake": True})
