from typing import Any

from django.contrib import admin
from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter
from unfold.decorators import action
from unfold.forms import BaseDialogForm

from apps.common.admin import BaseModelAdmin
from apps.common.admin import BaseTabularInline
from apps.common.admin import FieldPermissions
from apps.common.admin import confirm_dialog
from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.admin.resources import NotificationDeliveryResource
from apps.notifications.models import NotificationDelivery


class NeedsAttentionFilter(admin.SimpleListFilter):
    """The rows an operator must look at - the sidebar badge's list."""

    title = _("needs attention")
    parameter_name = "attention"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, Any]]:
        return [("yes", _("Needs attention"))]

    def queryset(
        self, request: HttpRequest, queryset: QuerySet[NotificationDelivery]
    ) -> Any:
        if self.value() == "yes":
            return queryset & selectors.deliveries_needing_attention()
        return queryset


class NotificationDeliveryInline(BaseTabularInline):
    """Per-channel outcome of the parent notification: display and
    navigation (show_change_link) only."""

    model = NotificationDelivery
    can_add = False
    can_change = False
    can_delete = False
    fields = (
        "channel",
        "status",
        "provider",
        "provider_message_id",
        "attempts",
        "sent_at",
        "detail",
    )


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(BaseModelAdmin):
    # Capability + field decisions for the NotificationDelivery admin.
    #
    # Rows are written by the delivery executor and status webhooks - the admin
    # inspects outcomes (and prunes) but never authors or edits them; resume goes
    # through the Broadcast actions / sweep_deliveries, not row edits.

    can_add = False
    can_change = False
    can_delete = True  # log pruning / cleanup
    field_permissions = FieldPermissions()
    list_display = (
        "notification",
        "channel",
        "status",
        "provider",
        "attempts",
        "sent_at",
        "created_at",
    )
    list_filter = (
        NeedsAttentionFilter,
        "channel",
        "status",
        ("created_at", RangeDateFilter),
    )
    list_filter_submit = True  # form-based (range) filters apply on submit
    search_fields = ("provider_message_id", "notification__recipient__email")
    search_help_text = _("Search by provider message id or recipient email.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("notification__recipient",)
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("notification", "broadcast", "channel", "status")}),
        (
            "Provider",
            {"fields": ("provider", "provider_message_id", "detail", "attempts")},
        ),
        ("Dates", {"fields": ("sent_at", "created_at", "updated_at")}),
    )
    readonly_fields = ()

    resource_classes = [NotificationDeliveryResource]

    # The recovery road for transactional sends (broadcasts resume from
    # their own page): re-queue what a dead worker left PROCESSING.
    actions_list = ["requeue_stuck"]

    def has_requeue_stuck_permission(
        self, request: HttpRequest, object_id: Any = None
    ) -> bool:
        return request.user.has_perm("notifications.change_notificationdelivery")

    @action(
        description=_("Re-queue stuck deliveries"),
        url_path="requeue-stuck",
        permissions=["requeue_stuck"],
        icon="replay",
        dialog=confirm_dialog(
            title=_("Re-queue stuck transactional deliveries?"),
            description=_(
                "Deliveries a worker left in progress for over 30 minutes go "
                "back to pending and are sent; nothing already sent is sent "
                "again. Failed rows stay failed (the provider rejected them)."
            ),
            submit=_("Re-queue"),
        ),
    )
    def requeue_stuck(self, request: HttpRequest, form: BaseDialogForm) -> HttpResponse:
        summary = services.deliveries_resume(broadcast=None, include_failed=False)
        messages.success(
            request,
            _("Re-queued: %(summary)s")
            % {"summary": ", ".join(f"{k}={v}" for k, v in summary.items())},
        )
        return redirect(reverse("admin:notifications_notificationdelivery_changelist"))
