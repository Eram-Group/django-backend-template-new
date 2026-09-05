from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.notifications.admin.resources import DeviceResource
from apps.notifications.models import Device


@admin.register(Device)
class DeviceAdmin(BaseModelAdmin):
    # Capability + field decisions for the Device admin.
    #
    # Rows are managed by the device_register/device_unregister services (and by
    # FCM invalid-token pruning) - the admin only inspects and force-deletes.

    can_add = False
    can_change = False
    can_delete = True  # force-detach a leaked/stolen device token
    field_permissions = FieldPermissions()
    list_display = ("user", "platform", "created_at", "updated_at")
    list_filter = ("platform",)
    list_filter_submit = False
    search_fields = ("user__email", "registration_id")
    search_help_text = _("Search by user email or registration token.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("user",)
    ordering = ("-updated_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("user", "registration_id", "platform")}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ()

    resource_classes = [DeviceResource]
