"""Broadcast fan-out: page the audience, materialize rows, enqueue batches.

Each page runs in ONE transaction that locks the Broadcast row (serializing
double-enqueued dispatchers), bulk-creates the inbox + delivery rows,
advances the cursor, and registers the batch enqueues on commit - so a
crashed dispatcher resumes exactly at its committed cursor and the partial
unique (broadcast, recipient) backstops any overlap.

Eligibility is decided HERE, at fan-out time: a delivery row exists only
for users who have the capability (a device for push, a phone for SMS/
WhatsApp) - the row's existence IS the decision. The executor still SKIPs
races (a device deleted mid-flight).
"""

import uuid
from functools import partial
from typing import Any

import structlog
from django.db import transaction
from django.tasks import task

from apps.notifications import selectors
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.tasks.delivery import chunk_ids
from apps.notifications.tasks.delivery import deliver_notifications
from apps.notifications.tasks.delivery import maybe_complete_broadcast

logger = structlog.get_logger(__name__)

BULK_QUEUE = "bulk"
DISPATCH_PAGE = 5000  # users per page = one transaction (~0.7s of bulk_create)
DELIVERY_BATCH = 200  # delivery rows per batch task = one FCM/OurSMS call


@task(queue_name=BULK_QUEUE)
def dispatch_broadcast(broadcast_id: str) -> None:
    pages = 0
    while _dispatch_one_page(broadcast_id=broadcast_id):
        pages += 1
    logger.info("broadcast_dispatched", broadcast_id=broadcast_id, pages=pages)


def _dispatch_one_page(*, broadcast_id: str) -> bool:
    """Materialize one audience page; returns True while more remain."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().get(pk=broadcast_id)
        if broadcast.status != BroadcastStatus.DISPATCHING:
            return False
        kind = NotificationKind(broadcast.kind)
        channels = sorted(selectors.effective_channels(kind=kind))
        audience = (
            selectors.broadcast_audience(broadcast=broadcast)
            .order_by("pk")
            .values_list("pk", "phone")
        )
        if broadcast.dispatch_cursor:
            audience = audience.filter(pk__gt=broadcast.dispatch_cursor)
        page = list(audience[:DISPATCH_PAGE])
        if page:
            deliveries = _materialize_page(
                broadcast=broadcast, page=page, channels=channels
            )
            broadcast.dispatch_cursor = page[-1][0]
            broadcast.total_recipients += len(page)
            broadcast.total_deliveries += len(deliveries)
            broadcast.save(
                update_fields=[
                    "dispatch_cursor",
                    "total_recipients",
                    "total_deliveries",
                    "updated_at",
                ]
            )
            for channel in channels:
                ids = [str(d.pk) for d in deliveries if d.channel == channel]
                for batch in chunk_ids(ids, size=DELIVERY_BATCH):
                    transaction.on_commit(
                        partial(
                            deliver_notifications.using(queue_name=BULK_QUEUE).enqueue,
                            batch,
                        )
                    )
        has_more = len(page) == DISPATCH_PAGE
        if not has_more:
            broadcast.status = BroadcastStatus.DISPATCHED
            broadcast.save(update_fields=["status", "updated_at"])
    if not has_more:
        # A zero-delivery broadcast (empty audience / no capable users) must
        # still finish; delivery batches also probe on their own completion.
        maybe_complete_broadcast(broadcast_id=broadcast_id)
    return has_more


def _materialize_page(
    *,
    broadcast: Broadcast,
    page: list[tuple[uuid.UUID, Any]],  # (user pk, phone)
    channels: list[Channel],
) -> list[NotificationDelivery]:
    user_ids = [pk for pk, _phone in page]
    has_device = set(
        Device.objects.filter(user_id__in=user_ids).values_list("user_id", flat=True)
    )
    notifications = [
        Notification(
            recipient_id=pk,
            kind=broadcast.kind,
            context=broadcast.context,
            broadcast=broadcast,
        )
        for pk, _phone in page
    ]
    Notification.objects.bulk_create(notifications, batch_size=1000)
    deliveries: list[NotificationDelivery] = []
    for (pk, phone), notification in zip(page, notifications, strict=True):
        for channel in channels:
            if channel == Channel.PUSH and pk not in has_device:
                continue
            if channel != Channel.PUSH and not phone:
                continue
            deliveries.append(
                NotificationDelivery(
                    notification=notification,
                    broadcast=broadcast,
                    channel=channel,
                )
            )
    NotificationDelivery.objects.bulk_create(deliveries, batch_size=1000)
    return deliveries
