"""Re-register allauth's UserSession admin for our username-less User.

Upstream UserSessionAdmin searches ``user__username``, which raises
FieldError against this project's email-only User model (caught by the
admin basics gate).
"""

from allauth.usersessions.admin import UserSessionAdmin
from allauth.usersessions.models import UserSession
from django.contrib import admin

admin.site.unregister(UserSession)


@admin.register(UserSession)
class ProjectUserSessionAdmin(UserSessionAdmin):
    search_fields = ["user__pk", "user__email", "ip"]
