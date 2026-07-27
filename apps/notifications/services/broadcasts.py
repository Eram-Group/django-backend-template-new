"""Broadcast lifecycle: author a draft, dispatch it, resume the remainder.

Dispatch is a guarded conditional UPDATE (DRAFT -> DISPATCHING; rowcount 0
= someone else already moved it) plus an on_commit enqueue of the
dispatcher task on the bulk queue - the request/admin transaction never
does fan-out work itself (ATOMIC_REQUESTS).

There is NO auto-retry by design: ``broadcast_resume`` is the explicit
recovery path, re-enqueuing exactly the rows that are still PENDING (plus
stale PROCESSING resets, plus FAILED when asked). At-least-once in crash
windows is accepted and documented.
"""

from datetime import timedelta
from functools import partial
from typing import Any

from django.db import transaction
from django.tasks import task_backends
from django.tasks.backends.immediate import ImmediateBackend
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.notifications import selectors
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import BroadcastStateError
from apps.notifications.exceptions import BroadcastTooLargeForInlineError
from apps.notifications.models import Broadcast
from apps.notifications.models import NotificationDelivery
from apps.notifications.services.notifications import _validate_context
from apps.notifications.tasks import deliver_notifications
from apps.notifications.tasks import dispatch_broadcast
from apps.notifications.tasks.broadcast import BULK_QUEUE
from apps.notifications.tasks.broadcast import DELIVERY_BATCH
from apps.notifications.tasks.delivery import chunk_ids
from apps.notifications.tasks.delivery import maybe_complete_broadcast
from apps.users.models import User

INLINE_AUDIENCE_LIMIT = 1000  # ImmediateBackend fan-out guard (local dev)


def notification_broadcast(
    *,
    kind: NotificationKind,
    context: dict[str, Any] | None = None,
    language: str = "",
    actor: User,
) -> Broadcast:
    """Author a DRAFT broadcast; nothing sends until broadcast_dispatch."""
    entry = catalog_entry(kind)
    resolved_context = dict(context or {})
    _validate_context(kind=kind, entry=entry, context=resolved_context)
    broadcast = Broadcast(
        kind=kind, context=resolved_context, language=language, created_by=actor
    )
    broadcast.full_clean()
    broadcast.save()
    return broadcast


def broadcast_dispatch(*, broadcast: Broadcast) -> Broadcast:
    """DRAFT -> DISPATCHING (guarded) + dispatcher enqueue on commit."""
    _guard_inline_backend(broadcast=broadcast)
    updated = Broadcast.objects.filter(
        pk=broadcast.pk, status=BroadcastStatus.DRAFT
    ).update(status=BroadcastStatus.DISPATCHING, updated_at=timezone.now())
    if not updated:
        raise BroadcastStateError(str(_("Broadcast was already dispatched.")))
    transaction.on_commit(lambda: dispatch_broadcast.enqueue(str(broadcast.pk)))
    broadcast.refresh_from_db()
    return broadcast


def broadcast_resume(
    *,
    broadcast: Broadcast,
    include_failed: bool = False,
    stale_minutes: int = 15,
) -> dict[str, int]:
    """Re-enqueue exactly the incomplete remainder, idempotently."""
    if broadcast.status == BroadcastStatus.DRAFT:
        raise BroadcastStateError(str(_("Broadcast has not been dispatched.")))
    now = timezone.now()
    cutoff = now - timedelta(minutes=stale_minutes)
    summary = {"dispatcher_reenqueued": 0, "stale_reset": 0, "failed_reset": 0}
    if broadcast.status == BroadcastStatus.DISPATCHING:
        # Dead dispatcher: the cursor committed with its rows, so a fresh
        # run continues exactly where it stopped.
        transaction.on_commit(lambda: dispatch_broadcast.enqueue(str(broadcast.pk)))
        summary["dispatcher_reenqueued"] = 1
    rows = NotificationDelivery.objects.filter(broadcast=broadcast)
    summary["stale_reset"] = rows.filter(
        status=DeliveryStatus.PROCESSING, updated_at__lt=cutoff
    ).update(status=DeliveryStatus.PENDING, updated_at=now)
    if include_failed:
        summary["failed_reset"] = rows.filter(status=DeliveryStatus.FAILED).update(
            status=DeliveryStatus.PENDING, detail="", updated_at=now
        )
    pending = list(
        rows.filter(status=DeliveryStatus.PENDING)
        .order_by("channel", "pk")
        .values_list("pk", flat=True)
    )
    for batch in chunk_ids([str(pk) for pk in pending], size=DELIVERY_BATCH):
        transaction.on_commit(
            partial(deliver_notifications.using(queue_name=BULK_QUEUE).enqueue, batch)
        )
    summary["re_enqueued"] = len(pending)
    if not pending:
        maybe_complete_broadcast(broadcast_id=broadcast.pk)
    return summary


def _guard_inline_backend(*, broadcast: Broadcast) -> None:
    """Refuse a big fan-out on the inline backend (local TASKS_IMMEDIATE)."""
    if not isinstance(task_backends["default"], ImmediateBackend):
        return
    audience = selectors.broadcast_audience(broadcast=broadcast).count()
    if audience > INLINE_AUDIENCE_LIMIT:
        message = _(
            "Audience of %(audience)s exceeds the inline-backend limit of "
            "%(limit)s - run a real worker (TASKS_IMMEDIATE=false)."
        ) % {"audience": audience, "limit": INLINE_AUDIENCE_LIMIT}
        raise BroadcastTooLargeForInlineError(str(message))
