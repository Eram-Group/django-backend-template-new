"""Changelist configuration for NotificationDelivery."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import enum_column
from apps.common.admin import enum_filter
from apps.notifications.constants import Channel

LIST_DISPLAY = (
    "notification",
    enum_column("channel", Channel, description=_("channel")),
    "status",
    "provider",
    "attempts",
    "sent_at",
    "created_at",
)
LIST_FILTER = (
    enum_filter("channel", Channel, title=_("channel")),
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
