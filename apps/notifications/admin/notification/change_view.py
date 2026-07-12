"""Change-form configuration for Notification (read-only inspection view)."""

FIELDSETS = (
    (None, {"fields": ("recipient", "kind", "context")}),
    ("Delivery", {"fields": ("read_at", "push_sent_at", "sms_sent_at")}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
