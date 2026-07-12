"""Inbox outputs. title/body are rendered from the catalog per response,
under the request locale (LocaleMiddleware / Accept-Language)."""

import uuid
from datetime import datetime

from ninja import Schema

from apps.notifications.catalog import notification_render
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification


class NotificationSummary(Schema):
    id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime

    @staticmethod
    def resolve_title(obj: Notification) -> str:
        return notification_render(
            kind=NotificationKind(obj.kind), context=obj.context
        ).title

    @staticmethod
    def resolve_body(obj: Notification) -> str:
        return notification_render(
            kind=NotificationKind(obj.kind), context=obj.context
        ).body


class UnreadCountOut(Schema):
    unread: int


class ReadAllOut(Schema):
    updated: int
