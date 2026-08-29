from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.notifications.admin.device import change_view
from apps.notifications.admin.device import list_view
from apps.notifications.admin.device import permissions
from apps.notifications.admin.device.resource import DeviceResource
from apps.notifications.models import Device


@admin.register(Device)
class DeviceAdmin(BaseModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [DeviceResource]

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS
