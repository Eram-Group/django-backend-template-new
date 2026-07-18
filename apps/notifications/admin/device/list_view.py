"""Changelist configuration for Device."""

from django.utils.translation import gettext_lazy as _

LIST_DISPLAY = ("user", "platform", "created_at", "updated_at")
LIST_FILTER = ("platform",)
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ("user__email", "registration_id")
SEARCH_HELP_TEXT = _("Search by user email or registration token.")
# FK columns render without a per-row query on the changelist.
LIST_SELECT_RELATED = ("user",)
ORDERING = ("-updated_at",)
LIST_PER_PAGE = 50
