"""Change-form configuration for Broadcast."""

FIELDSETS = (
    (None, {"fields": ("kind", "context", "language")}),
    (
        "Progress",
        {
            "fields": (
                "status",
                "dispatch_cursor",
                "total_recipients",
                "total_deliveries",
                "sent_count",
                "failed_count",
                "skipped_count",
            )
        },
    ),
    ("Meta", {"fields": ("created_by", "created_at", "updated_at")}),
)
READONLY_FIELDS = ()
