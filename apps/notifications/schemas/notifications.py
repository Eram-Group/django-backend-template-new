"""Inbox outputs. title/body render from each kind's config row per response,
under the request locale (LocaleMiddleware / Accept-Language)."""

import uuid
from datetime import datetime
from typing import Any

from ninja import Schema

from apps.notifications import selectors
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification


def _request_configs(context: dict[str, Any]) -> selectors.ConfigMap:
    """One config query per RESPONSE, not two per row.

    ninja calls each resolver per object; memoizing the 4-row map on the
    request keeps a page render at a single query while staying request-scoped
    (no cross-request cache - a live admin edit must show immediately).
    """
    request = context["request"]
    configs: selectors.ConfigMap | None = getattr(
        request, "_notification_configs", None
    )
    if configs is None:
        configs = selectors.notification_config_map()
        request._notification_configs = configs
    return configs


class NotificationSummary(Schema):
    id: uuid.UUID
    kind: NotificationKind
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime

    @staticmethod
    def resolve_title(obj: Notification, context: dict[str, Any]) -> str:
        return selectors.notification_render(
            kind=NotificationKind(obj.kind),
            context=obj.context,
            configs=_request_configs(context),
        ).title

    @staticmethod
    def resolve_body(obj: Notification, context: dict[str, Any]) -> str:
        return selectors.notification_render(
            kind=NotificationKind(obj.kind),
            context=obj.context,
            configs=_request_configs(context),
        ).body


class UnreadCountOut(Schema):
    unread: int


class ReadAllOut(Schema):
    updated: int


class DeleteAllOut(Schema):
    deleted: int
