"""Changelist configuration for Notification."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import enum_column
from apps.common.admin import enum_filter
from apps.notifications.constants import NotificationKind

LIST_DISPLAY = (
    "recipient",
    enum_column("kind", NotificationKind, description=_("kind")),
    "read_at",
    "broadcast",
    "created_at",
)
LIST_FILTER = (
    enum_filter("kind", NotificationKind, title=_("kind")),
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True  # form-based (range) filters apply on submit
SEARCH_FIELDS = ("recipient__email",)
SEARCH_HELP_TEXT = _("Search by recipient email.")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("recipient", "broadcast")
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
