from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.notifications.admin.notification_delivery import NotificationDeliveryInline
from apps.notifications.admin.resources import NotificationResource
from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(BaseModelAdmin):
    # Capability + field decisions for the Notification admin.
    #
    # Rows are created by services (notification_send / broadcast dispatch) and
    # only read/pruned here - operators never author or edit notifications by
    # hand, so the change form stays a read-only inspection view.

    can_add = False
    can_change = False
    can_delete = True  # inbox pruning / cleanup
    field_permissions = FieldPermissions()
    list_display = (
        "recipient",
        "kind",
        "read_at",
        "broadcast",
        "created_at",
    )
    list_filter = (
        "kind",
        ("created_at", RangeDateFilter),
    )
    list_filter_submit = True  # form-based (range) filters apply on submit
    search_fields = ("recipient__email",)
    search_help_text = _("Search by recipient email.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("recipient", "broadcast")
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("recipient", "kind", "context", "broadcast")}),
        ("Inbox", {"fields": ("read_at",)}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ()

    resource_classes = [NotificationResource]

    inlines = [NotificationDeliveryInline]
