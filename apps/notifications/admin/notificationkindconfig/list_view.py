"""Changelist configuration for NotificationKindConfig.

The changelist template is replaced by the single-page editor; these settings
still back the machinery underneath it (ordering, sorting-gate params).
"""

LIST_DISPLAY = (
    "kind",
    "updated_at",
)
LIST_FILTER = ()
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ()
SEARCH_HELP_TEXT = ""
ORDERING = ("kind",)
LIST_PER_PAGE = 50
