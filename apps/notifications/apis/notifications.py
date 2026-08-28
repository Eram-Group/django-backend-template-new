"""Inbox endpoints."""

import uuid

from django.db.models import QuerySet
from ninja import Router
from ninja.pagination import paginate
from ninja.responses import Status

from apps.common.pagination import CursorPagination
from apps.common.requests import AuthedRequest
from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.models import Notification
from apps.notifications.schemas import DeleteAllOut
from apps.notifications.schemas import NotificationSummary
from apps.notifications.schemas import ReadAllOut
from apps.notifications.schemas import UnreadCountOut
from apps.users.models import User

router = Router(tags=["notifications"])


@router.get("", response=list[NotificationSummary], summary="My notifications")
@paginate(CursorPagination)
def notification_list(request: AuthedRequest[User]) -> QuerySet[Notification]:
    return selectors.notification_list(user=request.auth)


@router.get("/unread-count", response=UnreadCountOut, summary="Unread count")
def notification_unread_count(request: AuthedRequest[User]) -> dict[str, int]:
    return {"unread": selectors.notification_unread_count(user=request.auth)}


@router.post("/read-all", response=ReadAllOut, summary="Mark all read")
def notification_read_all(request: AuthedRequest[User]) -> dict[str, int]:
    return {"updated": services.notification_mark_all_read(user=request.auth)}


@router.post("/delete-all", response=DeleteAllOut, summary="Clear my inbox")
def notification_delete_all(request: AuthedRequest[User]) -> dict[str, int]:
    return {"deleted": services.notification_delete_all(user=request.auth)}


@router.post(
    "/{uuid:notification_id}/read",
    response=NotificationSummary,
    summary="Mark one read",
)
def notification_read(
    request: AuthedRequest[User], notification_id: uuid.UUID
) -> Notification:
    return services.notification_mark_read(user=request.auth, pk=notification_id)


# The uuid: converter is load-bearing: a bare {notification_id} compiles to a
# [^/]+ wildcard that would swallow /preferences and /devices for DELETE.
@router.delete(
    "/{uuid:notification_id}", response={204: None}, summary="Delete one notification"
)
def notification_delete(
    request: AuthedRequest[User], notification_id: uuid.UUID
) -> Status[None]:
    services.notification_delete(user=request.auth, pk=notification_id)
    return Status(204, None)
