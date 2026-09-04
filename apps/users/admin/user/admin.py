from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

from apps.common.admin import BaseModelAdmin
from apps.users.admin.user.resource import UserResource
from apps.users.admin.user.sections import RecentSessionsSection
from apps.users.models import User


@admin.register(User)
class UserAdmin(BaseModelAdmin):
    resource_classes = [UserResource]

    list_sections = [RecentSessionsSection]

    def get_queryset(self, request: HttpRequest) -> QuerySet[User]:
        """Superuser accounts are managed only by superusers - to everyone
        else they don't exist (no change page, no changelist row)."""
        queryset: QuerySet[User] = super().get_queryset(request)
        if not request.user.is_superuser:
            queryset = queryset.filter(is_superuser=False)
        return queryset
