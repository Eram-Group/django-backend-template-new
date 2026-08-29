"""Reads for the notification inbox - always scoped to the recipient.

Rows whose kind was removed from ``NotificationKind`` are invisible (not
listed, fetched or counted): they can no longer be rendered, and hiding them
is what lets a kind be retired without a data migration.
"""

import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import NotificationNotFoundError
from apps.notifications.models import Notification
from apps.users.models import User


def _inbox(user: User) -> QuerySet[Notification]:
    return Notification.objects.filter(recipient=user, kind__in=NotificationKind.values)


def notification_list(*, user: User) -> QuerySet[Notification]:
    return _inbox(user)


def notification_get(*, user: User, pk: uuid.UUID) -> Notification:
    try:
        return _inbox(user).get(pk=pk)
    except Notification.DoesNotExist as exc:
        raise NotificationNotFoundError(str(_("Notification not found."))) from exc


def notification_unread_count(*, user: User) -> int:
    return _inbox(user).filter(read_at__isnull=True).count()
