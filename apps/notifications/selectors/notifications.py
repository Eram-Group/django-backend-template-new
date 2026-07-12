"""Reads for the notification inbox - always scoped to the recipient."""

import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.notifications.exceptions import NotificationNotFoundError
from apps.notifications.models import Notification
from apps.users.models import User


def notification_list(*, user: User) -> QuerySet[Notification]:
    return Notification.objects.filter(recipient=user)


def notification_get(*, user: User, pk: uuid.UUID) -> Notification:
    try:
        return Notification.objects.get(recipient=user, pk=pk)
    except Notification.DoesNotExist as exc:
        raise NotificationNotFoundError(str(_("Notification not found."))) from exc


def notification_unread_count(*, user: User) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()
