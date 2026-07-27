"""Change-form configuration for NotificationDelivery (read-only inspection)."""

FIELDSETS = (
    (None, {"fields": ("notification", "broadcast", "channel", "status")}),
    (
        "Provider",
        {"fields": ("provider", "provider_message_id", "detail", "attempts")},
    ),
    ("Dates", {"fields": ("sent_at", "created_at", "updated_at")}),
)
READONLY_FIELDS = ()
