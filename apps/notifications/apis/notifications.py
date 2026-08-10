"""Inbox endpoints."""

import uuid

from django.db.models import QuerySet
from ninja import Router
from ninja.pagination import paginate

from apps.common.pagination import CursorPagination
from apps.common.requests import AuthedRequest
from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.models import Notification
from apps.notifications.schemas import NotificationSummary
from apps.notifications.schemas import ReadAllOut
from apps.notifications.schemas import UnreadCountOut
from apps.users.models import User

router = Router(tags=["notifications"])


@router.get("", response=list[NotificationSummary], summary="My notifications")
@paginate(CursorPagination)
def notification_list(request: AuthedRequest[User]) -> QuerySet[Notification]:
    return selectors.list_user_notifications(user=request.auth)


@router.get("/unread-count", response=UnreadCountOut, summary="Unread count")
def notification_unread_count(request: AuthedRequest[User]) -> dict[str, int]:
    return {"unread": selectors.get_unread_notification_count(user=request.auth)}


@router.post("/read-all", response=ReadAllOut, summary="Mark all read")
def notification_read_all(request: AuthedRequest[User]) -> dict[str, int]:
    return {"updated": services.notification_mark_all_read(user=request.auth)}


@router.post(
    "/{notification_id}/read", response=NotificationSummary, summary="Mark one read"
)
def notification_read(
    request: AuthedRequest[User], notification_id: uuid.UUID
) -> Notification:
    return services.notification_mark_read(user=request.auth, pk=notification_id)
