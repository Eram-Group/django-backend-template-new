from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import BaseModelAdmin
from apps.common.admin import BaseTabularInline
from apps.common.admin import FieldPermissions
from apps.notifications.admin.resources import NotificationDeliveryResource
from apps.notifications.models import NotificationDelivery


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
