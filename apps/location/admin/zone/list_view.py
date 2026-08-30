"""Changelist configuration for Zone."""

from django.utils.translation import gettext_lazy as _

LIST_DISPLAY = ("code", "name", "country", "region_code", "is_active")
LIST_FILTER = ("is_active", "country", "region_code")
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ("code", "name_ar", "name_en", "region_code")
SEARCH_HELP_TEXT = _("Search by code, region or name (Arabic or English).")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("country",)
ORDERING = ("code",)
LIST_PER_PAGE = 50
