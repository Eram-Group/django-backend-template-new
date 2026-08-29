"""Changelist configuration for NotificationKindConfig.

The changelist template is replaced by the single-page editor; these settings
still back the machinery underneath it (ordering, sorting-gate params).
"""

from django.utils.translation import gettext_lazy as _

from apps.common.admin import enum_column
from apps.notifications.constants import NotificationKind

LIST_DISPLAY = (
    enum_column("kind", NotificationKind, description=_("action")),
    "updated_at",
)
LIST_FILTER = ()
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ()
SEARCH_HELP_TEXT = ""
ORDERING = ("kind",)
LIST_PER_PAGE = 50
