"""Changelist configuration for WalletTransaction."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

LIST_DISPLAY = ("wallet", "kind", "amount", "balance_after", "actor", "created_at")
LIST_FILTER = (
    "kind",
    ("created_at", RangeDateFilter),
)
LIST_FILTER_SUBMIT = True
SEARCH_FIELDS = ("wallet__user__email",)
SEARCH_HELP_TEXT = _("Search by wallet owner email.")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("wallet__user", "actor")
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
