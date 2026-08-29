"""Writes for the notification inbox + single-recipient fan-out.

No signals anywhere: other apps call ``notification_send`` directly (an
explicit cross-app service call), and delivery rides the caller's
transaction via on_commit - a rolled-back write never sends.
"""

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.notifications import selectors
from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.tasks import deliver_notifications
from apps.users.models import User


def _validate_context(
    *, kind: NotificationKind, entry: MessageTemplate, context: dict[str, Any]
) -> None:
    provided = frozenset(context)
    if provided != entry.context_keys:
        missing = sorted(entry.context_keys - provided)
        unexpected = sorted(provided - entry.context_keys)
        msg = (
            f"Context for NotificationKind.{kind.name} must have keys "
            f"{sorted(entry.context_keys)} (missing={missing}, "
            f"unexpected={unexpected})."
        )
        raise ValueError(msg)  # a wrong call site - programming error, no envelope


def notification_send(
    *,
    recipient: User,
    kind: NotificationKind,
    context: dict[str, Any],
) -> Notification:
    """Create the inbox row + one PENDING delivery row per resolved channel.

    The inbox row ALWAYS exists; channels come from the kind's config row.
    ``context`` must carry exactly the kind's ``context_keys`` - a kind with
    none takes ``{}``, explicitly.
    """
    entry = catalog_entry(kind)
    _validate_context(kind=kind, entry=entry, context=context)
    channels = selectors.effective_channels(kind=kind)
    notification = Notification(recipient=recipient, kind=kind, context=context)
    notification.full_clean()
    notification.save()
    deliveries = []
    for channel in sorted(channels):
        delivery = NotificationDelivery(notification=notification, channel=channel)
        delivery.full_clean()
        deliveries.append(delivery)
    NotificationDelivery.objects.bulk_create(deliveries)
    if deliveries:
        pending_ids = [str(delivery.pk) for delivery in deliveries]
        transaction.on_commit(lambda: deliver_notifications.enqueue(pending_ids))
    return notification


def notification_mark_read(*, user: User, pk: uuid.UUID) -> Notification:
    notification = selectors.notification_get(user=user, pk=pk)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.full_clean()
        notification.save(update_fields=["read_at", "updated_at"])
    return notification


def notification_mark_all_read(*, user: User) -> int:
    # Bulk .update() skips auto_now, so updated_at must be set explicitly.
    now = timezone.now()
    return Notification.objects.filter(recipient=user, read_at__isnull=True).update(
        read_at=now, updated_at=now
    )


def notification_delete(*, user: User, pk: uuid.UUID) -> None:
    """Delete one own inbox row (404 when not the caller's); delivery
    records cascade with it - the inbox is the user's to clear."""
    notification = selectors.notification_get(user=user, pk=pk)
    notification.delete()


def notification_delete_all(*, user: User) -> int:
    """Clear the caller's whole inbox; returns the notification count."""
    _total, per_model = Notification.objects.filter(recipient=user).delete()
    return per_model.get(Notification._meta.label, 0)
