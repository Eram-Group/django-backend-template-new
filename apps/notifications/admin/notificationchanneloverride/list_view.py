"""Changelist configuration for NotificationChannelOverride."""

LIST_DISPLAY = ("kind", "channel", "enabled", "updated_at")
LIST_FILTER = ("kind", "channel", "enabled")
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ()
SEARCH_HELP_TEXT = ""
ORDERING = ("kind", "channel")
LIST_PER_PAGE = 50
