from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from django.contrib import admin
from django.contrib import messages
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.decorators import action

from apps.common.admin import ExportableModelAdmin
from apps.common.exceptions import ApplicationError
from apps.notifications import services
from apps.notifications.admin.broadcast import change_view
from apps.notifications.admin.broadcast import list_view
from apps.notifications.admin.broadcast import permissions
from apps.notifications.admin.broadcast.resource import BroadcastResource
from apps.notifications.constants import BroadcastStatus
from apps.notifications.models import Broadcast

if TYPE_CHECKING:
    from apps.users.models import User


@admin.register(Broadcast)
class BroadcastAdmin(ExportableModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [BroadcastResource]

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS

    # Lifecycle buttons on the change form - each calls a service, never
    # obj.save(); the fan-out itself runs in the bulk-queue worker.
    actions_detail = ["dispatch_broadcast", "resume_broadcast"]

    def save_model(
        self, request: HttpRequest, obj: Model, form: ModelForm[Any], change: bool
    ) -> None:
        """Stamp the author on add - created_by is read-only, never typed."""
        if not change and isinstance(obj, Broadcast):
            obj.created_by = cast("User", request.user)
        super().save_model(request, obj, form, change)

    def has_dispatch_broadcast_permission(
        self, request: HttpRequest, object_id: str | None = None
    ) -> bool:
        if object_id is None:
            return True
        broadcast = Broadcast.objects.filter(pk=object_id).first()
        return broadcast is not None and broadcast.status == BroadcastStatus.DRAFT

    @action(
        description=_("Dispatch"),
        url_path="dispatch",
        permissions=["dispatch_broadcast"],
        icon="send",
    )
    def dispatch_broadcast(self, request: HttpRequest, object_id: str) -> HttpResponse:
        broadcast = Broadcast.objects.get(pk=object_id)
        try:
            services.broadcast_dispatch(broadcast=broadcast)
        except ApplicationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request, _("Dispatch started - refresh to follow progress.")
            )
        return redirect(
            reverse("admin:notifications_broadcast_change", args=[object_id])
        )

    def has_resume_broadcast_permission(
        self, request: HttpRequest, object_id: str | None = None
    ) -> bool:
        if object_id is None:
            return True
        broadcast = Broadcast.objects.filter(pk=object_id).first()
        return broadcast is not None and broadcast.status in (
            BroadcastStatus.DISPATCHING,
            BroadcastStatus.DISPATCHED,
        )

    @action(
        description=_("Resume incomplete"),
        url_path="resume",
        permissions=["resume_broadcast"],
        icon="replay",
    )
    def resume_broadcast(self, request: HttpRequest, object_id: str) -> HttpResponse:
        broadcast = Broadcast.objects.get(pk=object_id)
        try:
            summary = services.broadcast_resume(broadcast=broadcast)
        except ApplicationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request,
                _("Resume enqueued: %(summary)s")
                % {"summary": ", ".join(f"{k}={v}" for k, v in summary.items())},
            )
        return redirect(
            reverse("admin:notifications_broadcast_change", args=[object_id])
        )
