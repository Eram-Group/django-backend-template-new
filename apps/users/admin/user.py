from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import AdminContext
from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.common.admin import LimitedTableSection
from apps.common.admin import on_change
from apps.users.admin.resources import UserResource
from apps.users.models import User


def _not_superuser(context: AdminContext) -> bool:
    return not context.is_superuser


class RecentSessionsSection(LimitedTableSection):
    """Latest allauth sessions for the row's user - where and when they
    were last seen, capped to the newest five."""

    verbose_name = _("Recent sessions")
    related_name = "usersession_set"
    fields = ("ip", "user_agent", "last_seen_at")
    ordering = ("-last_seen_at",)
    limit = 5


@admin.register(User)
class UserAdmin(BaseModelAdmin):
    # Explicit capability + field decisions for the User admin.

    can_add = False  # users exist only via signup; superuser via `just superuser`
    can_change = True

    # Account removal is deactivation (users.user_deactivate): payments, wallets
    # and saved cards PROTECT their user FK, so a row delete of anyone who ever
    # paid raised ProtectedError in the admin.
    can_delete = False
    field_permissions = FieldPermissions(
        readonly_when={
            # The login identity: editing it in admin desyncs allauth's EmailAddress.
            "email": on_change,
            # Privilege surface: staff membership and group (= permission) grants
            # are superuser-only decisions.
            "is_staff": _not_superuser,
            "groups": _not_superuser,
        },
        hidden_when={
            # Non-superusers never see - or POST - the superuser bit.
            "is_superuser": _not_superuser,
        },
    )
    list_display = ("email", "name", "language", "is_active", "is_staff", "created_at")
    list_filter = (
        "is_active",
        "is_staff",
        "language",
        ("created_at", RangeDateFilter),
    )

    # Range filters are form-based: compose several filters, apply in one submit.
    list_filter_submit = True
    search_fields = ("email", "name", "phone")
    search_help_text = _("Search by email, full name, or phone number.")
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("email", "name", "phone", "language")}),
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
        (
            "Dates",
            {"fields": ("last_login", "date_joined", "created_at", "updated_at")},
        ),
    )
    readonly_fields = ("last_login", "date_joined")

    resource_classes = [UserResource]

    list_sections = [RecentSessionsSection]

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        """Superuser accounts are managed only by superusers - to everyone
        else they don't exist (no change page, no changelist row)."""
        queryset: QuerySet[User] = super().get_queryset(request)
        if not request.user.is_superuser:
            queryset = queryset.filter(is_superuser=False)
        return queryset
