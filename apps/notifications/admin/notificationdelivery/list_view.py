"""Changelist configuration for NotificationDelivery."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

LIST_DISPLAY = (
    "notification",
    "channel",
    "status",
    "provider",
    "attempts",
    "sent_at",
    "created_at",
)
LIST_FILTER = (
    "channel",
    "status",
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True  # form-based (range) filters apply on submit
SEARCH_FIELDS = ("provider_message_id", "notification__recipient__email")
SEARCH_HELP_TEXT = _("Search by provider message id or recipient email.")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("notification__recipient",)
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
