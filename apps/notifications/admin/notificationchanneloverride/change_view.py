"""Change-form configuration for NotificationChannelOverride."""

FIELDSETS = (
    (None, {"fields": ("kind", "channel", "enabled")}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
