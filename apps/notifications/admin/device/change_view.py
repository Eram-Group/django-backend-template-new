"""Change-form configuration for Device (read-only inspection view)."""

FIELDSETS = (
    (None, {"fields": ("user", "registration_id", "platform")}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
