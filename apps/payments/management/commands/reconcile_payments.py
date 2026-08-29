"""Scheduled sweep: recover payments stuck mid-flow.

Webhooks are the source of truth but can be lost, and the refund executor
task can die between its phases (django.tasks has no retries) - this command
is the retry mechanism for both. Cron: EventBridge Scheduler -> ECS run-task.

- PENDING rows older than ``PENDING_MAX_AGE`` are re-checked against the
  provider via ``payment_verify`` (the same idempotent transition a webhook
  drives); those still unpaid past ``constants.PENDING_EXPIRY`` are expired
  to FAILED (``payment_expire``) so abandoned checkouts stop occupying the
  oldest-first window.
- REFUND_PENDING rows older than ``REFUNDING_MAX_AGE`` are re-driven through
  ``payment_refund_execute`` when the provider was provably never contacted
  (``refund_attempted_at`` unset). Rows already attempted are only reported:
  the provider may have processed the refund, so they need manual
  reconciliation against its dashboard.

A provider call that fails is logged and the sweep moves on (the next run
retries), but the command then exits non-zero so the scheduled task alerts
instead of reporting a clean run.
"""

from datetime import datetime
from datetime import timedelta
from typing import Any

import structlog
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from apps.common.http import OutboundError
from apps.payments import services
from apps.payments.constants import PaymentStatus
from apps.payments.exceptions import PaymentError
from apps.payments.gateways.base import GatewayError
from apps.payments.models import Payment

logger = structlog.get_logger(__name__)

#: A PENDING row untouched this long has missed its webhook.
PENDING_MAX_AGE = timedelta(minutes=30)
#: A REFUND_PENDING row untouched this long has lost its executor task.
REFUNDING_MAX_AGE = timedelta(minutes=10)
#: Rows per status per run - oldest first; the next run takes the rest.
SWEEP_LIMIT = 500


class Command(BaseCommand):
    help = "Recover payments stuck in PENDING or REFUND_PENDING."

    def handle(self, *args: Any, **options: Any) -> None:
        now = timezone.now()
        verified, expired, pending_failures = self._sweep_pending(
            cutoff=now - PENDING_MAX_AGE
        )
        executed, reconcile, refund_failures = self._sweep_refunding(
            cutoff=now - REFUNDING_MAX_AGE
        )
        summary = (
            f"{verified} pending verified, {expired} expired, "
            f"{executed} refunds executed, {reconcile} need manual reconciliation"
        )
        failures = pending_failures + refund_failures
        if failures:
            msg = f"reconcile_payments: {failures} provider calls failed ({summary})"
            raise CommandError(msg)
        self.stdout.write(self.style.SUCCESS(f"reconcile_payments OK: {summary}"))

    def _sweep_pending(self, *, cutoff: datetime) -> tuple[int, int, int]:
        checked = 0
        expired = 0
        failures = 0
        stale = Payment.objects.filter(
            status=PaymentStatus.PENDING, updated_at__lt=cutoff
        ).order_by("updated_at")[:SWEEP_LIMIT]
        for payment in stale:
            try:
                verified = services.payment_verify(payment=payment)
            except (PaymentError, OutboundError, GatewayError) as exc:
                # Provider down or gateway unconfigured: next run retries.
                failures += 1
                logger.warning(
                    "payment_pending_sweep_skipped",
                    payment_id=str(payment.pk),
                    error=type(exc).__name__,
                )
                continue
            checked += 1
            if verified.status == PaymentStatus.PENDING:
                # Nothing paid: expire it once it is older than any gateway's
                # hosted-session lifetime (no-op while still fresh).
                verified = services.payment_expire(payment=verified)
                if verified.status == PaymentStatus.FAILED:
                    expired += 1
        return checked, expired, failures

    def _sweep_refunding(self, *, cutoff: datetime) -> tuple[int, int, int]:
        executed = 0
        failures = 0
        stale = Payment.objects.filter(
            status=PaymentStatus.REFUND_PENDING, updated_at__lt=cutoff
        ).order_by("updated_at")[:SWEEP_LIMIT]
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
            except (PaymentError, OutboundError, GatewayError) as exc:
                # PaymentError subtypes mean the interlock was reverted (safe
                # state); raw outbound errors were already logged by the
                # executor as needing reconciliation. Either way: counted.
                failures += 1
                logger.warning(
                    "payment_refund_sweep_failed",
                    payment_id=str(payment.pk),
                    error=type(exc).__name__,
                )
            else:
                executed += 1
        return executed, needs_reconciliation, failures
