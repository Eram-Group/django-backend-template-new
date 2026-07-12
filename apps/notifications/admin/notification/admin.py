from django.contrib import admin

from apps.common.admin import ExportableModelAdmin
from apps.notifications.admin.notification import change_view
from apps.notifications.admin.notification import list_view
from apps.notifications.admin.notification import permissions
from apps.notifications.admin.notification.resource import NotificationResource
from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(ExportableModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [NotificationResource]

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS

    # inlines = [...]        # child rows on the change form (inline.py)
    # list_sections = [...]  # expandable per-row previews (LimitedTableSection)
    # actions_detail = [...] # state-transition buttons - the body calls a
    #                        # service, never obj.save() (see ARCHITECTURE.md)
