from django.contrib import admin

from apps.common.admin import ExportableModelAdmin
from apps.users.admin.user import change_view
from apps.users.admin.user import list_view
from apps.users.admin.user import permissions
from apps.users.admin.user.resource import UserResource
from apps.users.models import User


@admin.register(User)
class UserAdmin(ExportableModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [UserResource]

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    search_fields = list_view.SEARCH_FIELDS
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS
    filter_horizontal = ("groups",)
