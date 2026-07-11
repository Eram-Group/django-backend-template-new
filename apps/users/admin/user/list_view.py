"""Changelist configuration for User."""

LIST_DISPLAY = ("email", "name", "language", "is_active", "is_staff", "created_at")
LIST_FILTER = ("is_active", "is_staff", "language")
SEARCH_FIELDS = ("email", "name")
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
