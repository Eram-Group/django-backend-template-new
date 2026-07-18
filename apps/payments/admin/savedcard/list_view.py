"""Changelist configuration for SavedCard."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

LIST_DISPLAY = (
    "user",
    "gateway",
    "brand",
    "last4",
    "exp_month",
    "exp_year",
    "created_at",
)
LIST_FILTER = (
    "gateway",
    "brand",
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True
SEARCH_FIELDS = ("user__email", "token")
SEARCH_HELP_TEXT = _("Search by owner email or gateway token.")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("user",)
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
