"""Change-form configuration for Notification (read-only inspection view)."""

FIELDSETS = (
    (None, {"fields": ("recipient", "kind", "context", "broadcast")}),
    ("Inbox", {"fields": ("read_at",)}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
