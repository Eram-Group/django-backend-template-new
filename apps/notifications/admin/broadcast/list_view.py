"""Changelist configuration for Broadcast."""

from unfold.contrib.filters.admin import RangeDateFilter

LIST_DISPLAY = (
    "kind",
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
    "kind",
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True  # form-based (range) filters apply on submit
SEARCH_FIELDS = ()
SEARCH_HELP_TEXT = ""
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
