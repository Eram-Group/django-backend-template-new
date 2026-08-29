"""Changelist configuration for Broadcast."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import enum_column
from apps.common.admin import enum_filter
from apps.notifications.constants import NotificationKind

LIST_DISPLAY = (
    enum_column("kind", NotificationKind, description=_("kind")),
    "status",
    "language",
    "total_recipients",
    "sent_count",
    "failed_count",
    "skipped_count",
    "created_at",
)
LIST_FILTER = (
    "status",
    enum_filter("kind", NotificationKind, title=_("kind")),
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True  # form-based (range) filters apply on submit
SEARCH_FIELDS = ()
SEARCH_HELP_TEXT = ""
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
