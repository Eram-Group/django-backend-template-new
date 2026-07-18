"""Scheduled sweep: recover payments stuck mid-flow.

Webhooks are the source of truth but can be lost, and the refund executor
task can die between its phases (django.tasks has no retries) - this command
is the retry mechanism for both. Cron: EventBridge Scheduler -> ECS run-task.

- PENDING rows older than ``--pending-max-age-minutes`` are re-checked
  against the provider via ``payment_verify`` (the same idempotent
  transition a webhook drives).
- REFUND_PENDING rows older than ``--refunding-max-age-minutes`` are
  re-driven through ``payment_refund_execute`` when the provider was
  provably never contacted (``refund_attempted_at`` unset). Rows already
  attempted are only reported: the provider may have processed the refund,
  so they need manual reconciliation against its dashboard.
"""

from datetime import datetime
from datetime import timedelta
from typing import Any

import structlog
from django.core.management.base import BaseCommand
from django.core.management.base import CommandParser
from django.utils import timezone

from apps.common.http import OutboundError
from apps.payments import services
from apps.payments.constants import PaymentStatus
from apps.payments.exceptions import PaymentError
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.models import Payment

logger = structlog.get_logger(__name__)


class Command(BaseCommand):
    help = "Recover payments stuck in PENDING or REFUND_PENDING."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--pending-max-age-minutes", type=int, default=30)
        parser.add_argument("--refunding-max-age-minutes", type=int, default=10)
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args: Any, **options: Any) -> None:
        limit: int = options["limit"]
        now = timezone.now()
        verified = self._sweep_pending(
            cutoff=now - timedelta(minutes=options["pending_max_age_minutes"]),
            limit=limit,
        )
        executed, reconcile = self._sweep_refunding(
            cutoff=now - timedelta(minutes=options["refunding_max_age_minutes"]),
            limit=limit,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"reconcile_payments OK: {verified} pending verified, "
                f"{executed} refunds executed, {reconcile} need manual reconciliation"
            )
        )

    def _sweep_pending(self, *, cutoff: datetime, limit: int) -> int:
        checked = 0
        stale = Payment.objects.filter(
            status=PaymentStatus.PENDING, updated_at__lt=cutoff
        ).order_by("updated_at")[:limit]
        for payment in stale:
            try:
                services.payment_verify(payment=payment)
            except (PaymentError, OutboundError, GatewayResponseError) as exc:
                # Provider down or gateway unconfigured: next run retries.
                logger.warning(
                    "payment_pending_sweep_skipped",
                    payment_id=str(payment.pk),
                    error=type(exc).__name__,
                )
            else:
                checked += 1
        return checked

    def _sweep_refunding(self, *, cutoff: datetime, limit: int) -> tuple[int, int]:
        executed = 0
        stale = Payment.objects.filter(
            status=PaymentStatus.REFUND_PENDING, updated_at__lt=cutoff
        ).order_by("updated_at")[:limit]
        needs_reconciliation = 0
        for payment in stale:
            if payment.refund_attempted_at is not None:
                # The provider may already have processed this refund -
                # never re-send it; a human reconciles against the dashboard.
                needs_reconciliation += 1
                logger.error(
                    "payment_refund_needs_reconciliation",
                    payment_id=str(payment.pk),
                    gateway=payment.gateway,
                    refund_attempted_at=payment.refund_attempted_at.isoformat(),
                )
                continue
            try:
                services.payment_refund_execute(payment_id=payment.pk, actor=None)
            except (PaymentError, OutboundError, GatewayResponseError) as exc:
                # PaymentError subtypes mean the interlock was reverted (safe
                # state); raw outbound errors were already logged by the
                # executor as needing reconciliation. Either way: continue.
                logger.warning(
                    "payment_refund_sweep_failed",
                    payment_id=str(payment.pk),
                    error=type(exc).__name__,
                )
            else:
                executed += 1
        return executed, needs_reconciliation
