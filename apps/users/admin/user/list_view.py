"""Changelist configuration for User."""

from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

LIST_DISPLAY = ("email", "name", "language", "is_active", "is_staff", "created_at")
LIST_FILTER = (
    "is_active",
    "is_staff",
    "language",
    ("created_at", RangeDateFilter),
)
# Range filters are form-based: compose several filters, apply in one submit.
LIST_FILTER_SUBMIT = True
SEARCH_FIELDS = ("email", "name", "phone")
SEARCH_HELP_TEXT = _("Search by email, full name, or phone number.")
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
