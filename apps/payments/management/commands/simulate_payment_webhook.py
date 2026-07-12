"""Locally simulate the gateway webhook for a pending payment.

    manage.py simulate_payment_webhook <payment-pk>          # mark paid
    manage.py simulate_payment_webhook <payment-pk> --fail   # mark failed

Drives the SAME transition service the real webhook endpoint calls
(payment_apply_gateway_event), so wallet credit + notification fan-out run
exactly as in production - Mailpit's role, for payments. Local-only.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from config.env import env


class Command(BaseCommand):
    help = "Simulate the gateway webhook for a payment (local only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("payment_pk", help="Payment pk (uuid).")
        parser.add_argument(
            "--fail", action="store_true", help="Deliver a failed event instead."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if env.ENVIRONMENT != "local":
            msg = (
                "simulate_payment_webhook only runs locally "
                f"(ENVIRONMENT={env.ENVIRONMENT})."
            )
            raise CommandError(msg)

        from apps.payments import services
        from apps.payments.exceptions import PaymentError
        from apps.payments.gateways.base import WebhookEvent
        from apps.payments.models import Payment

        try:
            payment = Payment.objects.get(pk=options["payment_pk"])
        except (Payment.DoesNotExist, ValueError) as exc:
            msg = f"No payment with pk {options['payment_pk']}."
            raise CommandError(msg) from exc

        paid = not options["fail"]
        event = WebhookEvent(
            reference=str(payment.idempotency_key),
            transaction_id=f"fake_txn_{payment.pk}",
            is_paid=paid,
            status="PAID" if paid else "FAILED",
            raw={"simulated": True},
        )
        try:
            payment = services.payment_apply_gateway_event(
                gateway_name=payment.gateway, event=event
            )
        except PaymentError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(f"payment {payment.pk} -> {payment.status}")
        )
