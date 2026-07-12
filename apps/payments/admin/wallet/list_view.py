"""Changelist configuration for Wallet."""

from django.utils.translation import gettext_lazy as _

LIST_DISPLAY = ("user", "balance", "currency", "updated_at")
LIST_FILTER = ("currency",)
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ("user__email",)
SEARCH_HELP_TEXT = _("Search by user email.")
ORDERING = ("-updated_at",)
LIST_PER_PAGE = 50
