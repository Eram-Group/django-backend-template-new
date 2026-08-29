"""Re-register django.contrib.auth's Group admin on the framework.

The stock GroupAdmin is not an unfold ModelAdmin: it renders unstyled
widgets inside the unfold shell and escapes the can_* discipline. Groups
are the permission-management surface for the whole admin - treat them
like a first-party entity (same precedent as user_session.py).
"""

from django.contrib import admin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from apps.common.admin import BaseModelAdmin
from apps.common.admin import BaseModelResource

admin.site.unregister(Group)


class GroupResource(BaseModelResource):
    class Meta:
        model = Group
        fields = ("id", "name")


@admin.register(Group)
class ProjectGroupAdmin(BaseModelAdmin):
    can_add = True
    can_change = True
    can_delete = True
    resource_classes = [GroupResource]

    list_display = ("name",)
    search_fields = ("name",)
    search_help_text = _("Search by group name.")
    ordering = ("name",)
