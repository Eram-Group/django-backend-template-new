"""Reads for the notification inbox - always scoped to the recipient."""

import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.notifications.exceptions import NotificationNotFoundError
from apps.notifications.models import Notification
from apps.users.models import User


def list_user_notifications(*, user: User) -> QuerySet[Notification]:
    return Notification.objects.filter(recipient=user)


def get_notification_for_user(*, user: User, pk: uuid.UUID) -> Notification:
    try:
        return Notification.objects.get(recipient=user, pk=pk)
    except Notification.DoesNotExist as exc:
        raise NotificationNotFoundError(str(_("Notification not found."))) from exc


def get_unread_notification_count(*, user: User) -> int:
    return Notification.objects.filter(recipient=user, read_at__isnull=True).count()
