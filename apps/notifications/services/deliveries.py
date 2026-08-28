"""Delivery-status ingestion (webhooks) + the transactional sweep.

``delivery_update_status`` is ONE conditional UPDATE with a monotonic
guard: statuses only move forward (PENDING < PROCESSING < SENT < DELIVERED
< READ; FAILED only from at-most-SENT), so duplicate or out-of-order
webhooks are rowcount-0 no-ops. An unknown provider message id logs and
acks - telemetry, not money; a Meta retry storm must not build.
"""

from datetime import timedelta
from functools import partial
from typing import Any

import structlog
from django.db import transaction
from django.utils import timezone

from apps.notifications.constants import DeliveryStatus
from apps.notifications.models import NotificationDelivery
from apps.notifications.tasks import deliver_notifications
from apps.notifications.tasks.delivery import chunk_ids

logger = structlog.get_logger(__name__)

_FORWARD_RANK: dict[DeliveryStatus, int] = {
    DeliveryStatus.PENDING: 0,
    DeliveryStatus.PROCESSING: 1,
    DeliveryStatus.SENT: 2,
    DeliveryStatus.DELIVERED: 3,
    DeliveryStatus.READ: 4,
}
_FAILED_ALLOWED_FROM = (
    DeliveryStatus.PENDING,
    DeliveryStatus.PROCESSING,
    DeliveryStatus.SENT,
)
SWEEP_BATCH = 200


def delivery_update_status(
    *,
    provider: str,
    provider_message_id: str,
    status: DeliveryStatus,
    detail: str = "",
) -> bool:
    """Apply one provider status report; returns False when ignored."""
    if status == DeliveryStatus.FAILED:
        allowed_from: tuple[DeliveryStatus, ...] = _FAILED_ALLOWED_FROM
    elif status in _FORWARD_RANK:
        target_rank = _FORWARD_RANK[status]
        allowed_from = tuple(
            source for source, rank in _FORWARD_RANK.items() if rank < target_rank
        )
    else:  # SKIPPED never arrives from a provider
        allowed_from = ()
    if not allowed_from:
        return False
    now = timezone.now()
    updates: dict[str, Any] = {"status": status, "updated_at": now}
    if detail:
        updates["detail"] = detail
    if status == DeliveryStatus.SENT:
        updates["sent_at"] = now
    updated = NotificationDelivery.objects.filter(
        provider=provider,
        provider_message_id=provider_message_id,
        status__in=allowed_from,
    ).update(**updates)
    if not updated:
        logger.info(
            "delivery_status_ignored",
            provider=provider,
            provider_message_id=provider_message_id,
            status=str(status),
        )
    return bool(updated)


def deliveries_sweep_transactional(
    *, include_failed: bool = False, stale_minutes: int = 30
) -> dict[str, int]:
    """Recover orphaned single-send deliveries (no broadcast to resume).

    Stale PROCESSING rows (worker died mid-batch) reset to PENDING; FAILED
    rows optionally reset; every PENDING row older than the cutoff is
    re-enqueued in batches. Idempotent - the executor's claim makes an
    over-enqueued row a no-op.
    """
    now = timezone.now()
    cutoff = now - timedelta(minutes=stale_minutes)
    orphans = NotificationDelivery.objects.filter(broadcast__isnull=True)
    stale_reset = orphans.filter(
        status=DeliveryStatus.PROCESSING, updated_at__lt=cutoff
    ).update(status=DeliveryStatus.PENDING, updated_at=now)
    failed_reset = 0
    if include_failed:
        failed_reset = orphans.filter(status=DeliveryStatus.FAILED).update(
            status=DeliveryStatus.PENDING, detail="", updated_at=now
        )
    pending_ids = [
        str(pk)
        for pk in orphans.filter(
            status=DeliveryStatus.PENDING, updated_at__lte=now
        ).values_list("pk", flat=True)
    ]
    for batch in chunk_ids(pending_ids, size=SWEEP_BATCH):
        transaction.on_commit(partial(deliver_notifications.enqueue, batch))
    return {
        "stale_reset": stale_reset,
        "failed_reset": failed_reset,
        "re_enqueued": len(pending_ids),
    }
