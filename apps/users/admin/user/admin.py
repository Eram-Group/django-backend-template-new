from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.common.admin import ExportableModelAdmin
from apps.users.admin.user import change_view
from apps.users.admin.user import list_view
from apps.users.admin.user import permissions
from apps.users.admin.user.resource import UserResource
from apps.users.admin.user.sections import RecentSessionsSection
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
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE
    list_sections = [RecentSessionsSection]

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS
    filter_horizontal = ("groups",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        """Superuser accounts are managed only by superusers - to everyone
        else they don't exist (no change page, no changelist row)."""
        queryset: QuerySet[User] = super().get_queryset(request)
        if not getattr(request.user, "is_superuser", False):
            queryset = queryset.filter(is_superuser=False)
        return queryset
