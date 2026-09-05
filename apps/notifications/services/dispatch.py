"""Broadcast fan-out: page the audience, materialize rows, enqueue batches.

One dispatcher task = ONE page (``broadcast_dispatch_page``): a transaction
that locks the Broadcast row (serializing double-enqueued dispatchers),
bulk-creates the inbox + delivery rows, advances the cursor, queues the
delivery batches (the queue is this database: the task rows commit with
the delivery rows) and, while more remain, re-enqueues the dispatcher for
the next page. A crashed dispatcher resumes exactly at its committed
cursor, the partial unique (broadcast, recipient) backstops any overlap,
and no single task ever holds a 100k-user loop.

Eligibility is decided HERE, at fan-out time: a delivery row exists only
for users who have the capability (a device for push, a phone for SMS/
WhatsApp) - the row's existence IS the decision. The executor still SKIPs
races (a device deleted mid-flight).
"""

import uuid
from itertools import batched
from typing import Any

import structlog
from django.db import transaction

from apps.notifications import selectors
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.services.execution import maybe_complete_broadcast
from apps.notifications.tasks.broadcast import BULK_QUEUE
from apps.notifications.tasks.broadcast import dispatch_broadcast
from apps.notifications.tasks.delivery import deliver_notifications

logger = structlog.get_logger(__name__)

DISPATCH_PAGE = 1000  # users per page = one transaction = one task
DELIVERY_BATCH = 200  # delivery rows per batch task = one FCM/OurSMS call


def broadcast_dispatch_page(*, broadcast_id: str) -> bool:
    """Materialize one audience page; re-enqueues the dispatcher while more
    remain. Returns True when another page follows."""
    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().get(pk=broadcast_id)
        if broadcast.status != BroadcastStatus.DISPATCHING:
            return False
        kind = NotificationKind(broadcast.kind)
        channels = sorted(selectors.effective_channels(kind=kind, broadcast=broadcast))
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
                enqueue = deliver_notifications.using(queue_name=BULK_QUEUE).enqueue
                for batch in batched(ids, DELIVERY_BATCH, strict=False):
                    enqueue(list(batch))  # same transaction as the rows
        has_more = len(page) == DISPATCH_PAGE
        if has_more:
            dispatch_broadcast.enqueue(broadcast_id)  # commits with the cursor
        else:
            broadcast.status = BroadcastStatus.DISPATCHED
            broadcast.save(update_fields=["status", "updated_at"])
            logger.info(
                "broadcast_dispatched",
                broadcast_id=broadcast_id,
                recipients=broadcast.total_recipients,
            )
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
