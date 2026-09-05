"""Delivery-status ingestion (webhooks) + the ONE recovery path.

``delivery_update_status`` is ONE conditional UPDATE with a monotonic
guard: statuses only move forward (PENDING < PROCESSING < SENT < DELIVERED
< READ; FAILED only from at-most-SENT), so duplicate or out-of-order
webhooks are rowcount-0 no-ops. An unknown provider message id logs and
acks - telemetry, not money; a Meta retry storm must not build.

``deliveries_resume`` recovers incomplete deliveries - for one broadcast
(the admin "Resume incomplete" action) or for the transactional orphans
(the ``sweep_deliveries`` command). There is no auto-retry by
design; this is the explicit, idempotent re-enqueue.
"""

from datetime import timedelta
from itertools import batched
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import DeliveryStatus
from apps.notifications.exceptions import BroadcastStateError
from apps.notifications.models import Broadcast
from apps.notifications.models import NotificationDelivery
from apps.notifications.services.dispatch import DELIVERY_BATCH
from apps.notifications.services.execution import maybe_complete_broadcast
from apps.notifications.services.progress import broadcast_record_progress
from apps.notifications.tasks import deliver_notifications
from apps.notifications.tasks import dispatch_broadcast
from apps.notifications.tasks.broadcast import BULK_QUEUE

logger = structlog.get_logger(__name__)

#: Provider-reported target -> the statuses it may move a row FROM. Only what
#: a provider can report is listed: PENDING/PROCESSING/SKIPPED are ours, and
#: passing one here is a programming error (KeyError).
_ALLOWED_FROM: dict[DeliveryStatus, tuple[DeliveryStatus, ...]] = {
    DeliveryStatus.SENT: (DeliveryStatus.PENDING, DeliveryStatus.PROCESSING),
    DeliveryStatus.DELIVERED: (
        DeliveryStatus.PENDING,
        DeliveryStatus.PROCESSING,
        DeliveryStatus.SENT,
    ),
    DeliveryStatus.READ: (
        DeliveryStatus.PENDING,
        DeliveryStatus.PROCESSING,
        DeliveryStatus.SENT,
        DeliveryStatus.DELIVERED,
    ),
    DeliveryStatus.FAILED: (
        DeliveryStatus.PENDING,
        DeliveryStatus.PROCESSING,
        DeliveryStatus.SENT,
    ),
}

#: A PROCESSING row untouched this long was claimed by a worker that died
#: mid-batch (a batch is one FCM/OurSMS call - seconds, not minutes).
STALE_PROCESSING_MINUTES = 30
#: Rows one resume call re-enqueues at most (oldest first); the sweep runs
#: every tick and the admin action can be repeated, so a backlog drains in
#: bounded steps instead of one unbounded transaction.
RESUME_LIMIT = 10_000


def delivery_update_status(
    *,
    provider: str,
    provider_message_id: str,
    status: DeliveryStatus,
    detail: str = "",
) -> bool:
    """Apply one provider status report; returns False when ignored.

    A provider can fail a row we already counted as SENT (a bounced
    WhatsApp message): the broadcast counters move with the row, in the
    same transaction.
    """
    allowed_from = _ALLOWED_FROM[status]
    now = timezone.now()
    updates: dict[str, Any] = {"status": status, "updated_at": now}
    if detail:
        updates["detail"] = detail
    if status == DeliveryStatus.SENT:
        updates["sent_at"] = now
    with transaction.atomic():
        row = (
            NotificationDelivery.objects.select_for_update()
            .filter(
                provider=provider,
                provider_message_id=provider_message_id,
                status__in=allowed_from,
            )
            .first()
        )
        if row is None:
            logger.info(
                "delivery_status_ignored",
                provider=provider,
                provider_message_id=provider_message_id,
                status=str(status),
            )
            return False
        NotificationDelivery.objects.filter(pk=row.pk).update(**updates)
        if (
            row.broadcast_id is not None
            and status == DeliveryStatus.FAILED
            and row.status == DeliveryStatus.SENT
        ):
            broadcast_record_progress(broadcast_id=row.broadcast_id, sent=-1, failed=1)
    return True


def deliveries_resume(
    *, broadcast: Broadcast | None, include_failed: bool
) -> dict[str, int]:
    """Re-enqueue exactly the incomplete remainder, idempotently.

    ``broadcast=None`` scopes to transactional sends (rows with no
    broadcast); a broadcast scopes to its rows, re-runs a dead dispatcher
    (the cursor committed with its rows, so a fresh run continues where it
    stopped) and probes completion. Stale PROCESSING rows reset to PENDING;
    FAILED rows reset when asked (their ``failed_count`` moves with them, so
    a retried row is counted once); up to ``RESUME_LIMIT`` PENDING rows are
    re-enqueued in executor-sized batches - ``remaining`` reports what the
    next call takes. Over-enqueueing is harmless - the executor's claim
    makes an already-taken row a no-op.
    """
    with transaction.atomic():
        now = timezone.now()
        cutoff = now - timedelta(minutes=STALE_PROCESSING_MINUTES)
        summary: dict[str, int] = {}
        if broadcast is None:
            rows = NotificationDelivery.objects.filter(broadcast__isnull=True)
            enqueue = deliver_notifications.enqueue
        else:
            if broadcast.status == BroadcastStatus.DRAFT:
                raise BroadcastStateError(str(_("Broadcast has not been dispatched.")))
            summary["dispatcher_reenqueued"] = 0
            if broadcast.status == BroadcastStatus.DISPATCHING:
                dispatch_broadcast.enqueue(str(broadcast.pk))
                summary["dispatcher_reenqueued"] = 1
            rows = NotificationDelivery.objects.filter(broadcast=broadcast)
            enqueue = deliver_notifications.using(queue_name=BULK_QUEUE).enqueue
        summary["stale_reset"] = rows.filter(
            status=DeliveryStatus.PROCESSING, updated_at__lt=cutoff
        ).update(status=DeliveryStatus.PENDING, updated_at=now)
        summary["failed_reset"] = 0
        if include_failed:
            summary["failed_reset"] = rows.filter(status=DeliveryStatus.FAILED).update(
                status=DeliveryStatus.PENDING, detail="", updated_at=now
            )
            if broadcast is not None and summary["failed_reset"]:
                # Back to uncounted: the executor counts the retry's outcome.
                broadcast_record_progress(
                    broadcast_id=broadcast.pk, failed=-summary["failed_reset"]
                )
        pending_rows = rows.filter(status=DeliveryStatus.PENDING).order_by(
            "channel", "pk"
        )
        pending = [
            str(pk) for pk in pending_rows.values_list("pk", flat=True)[:RESUME_LIMIT]
        ]
        for batch in batched(pending, DELIVERY_BATCH, strict=False):
            enqueue(list(batch))  # task rows commit with the resets
        summary["re_enqueued"] = len(pending)
        summary["remaining"] = max(pending_rows.count() - len(pending), 0)
        if broadcast is not None and not pending:
            maybe_complete_broadcast(broadcast_id=broadcast.pk)
        return summary
