"""Expandable per-row related-record previews on the User changelist."""

from django.utils.translation import gettext_lazy as _

from apps.common.admin import LimitedTableSection


class RecentSessionsSection(LimitedTableSection):
    """Latest allauth sessions for the row's user - where and when they
    were last seen, capped to the newest five."""

    verbose_name = _("Recent sessions")
    related_name = "usersession_set"
    fields = ("ip", "user_agent", "last_seen_at")
    ordering = ("-last_seen_at",)
    limit = 5
