"""Changelist configuration for Payment."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

LIST_DISPLAY = ("user", "amount", "currency", "kind", "status", "gateway", "created_at")
LIST_FILTER = (
    "status",
    "gateway",
    "currency",
    "kind",
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True  # form-based (range) filters apply on submit
SEARCH_FIELDS = ("user__email", "gateway_charge_id", "gateway_transaction_id")
SEARCH_HELP_TEXT = _("Search by user email or gateway charge/transaction id.")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("user",)
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
