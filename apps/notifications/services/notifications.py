"""Writes for the notification inbox + delivery fan-out.

No signals anywhere: other apps call ``notification_send`` directly (an
explicit cross-app service call), and delivery tasks ride the caller's
transaction via on_commit - a rolled-back write never pushes.
"""

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.notifications import selectors
from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.notifications.tasks import send_push_notification
from apps.notifications.tasks import send_sms_notification
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
    context: dict[str, Any] | None = None,
) -> Notification:
    """Create the inbox row and fan out to the kind's channels."""
    entry = catalog_entry(kind)
    resolved_context = dict(context or {})
    _validate_context(kind=kind, entry=entry, context=resolved_context)
    notification = Notification(
        recipient=recipient, kind=kind, context=resolved_context
    )
    notification.full_clean()
    notification.save()
    if Channel.PUSH in entry.channels:
        transaction.on_commit(
            lambda: send_push_notification.enqueue(str(notification.pk))
        )
    if Channel.SMS in entry.channels:
        transaction.on_commit(
            lambda: send_sms_notification.enqueue(str(notification.pk))
        )
    return notification


def notification_mark_read(*, user: User, pk: uuid.UUID) -> Notification:
    notification = selectors.get_notification_for_user(user=user, pk=pk)
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
