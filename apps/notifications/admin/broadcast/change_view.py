"""Change-form configuration for Broadcast."""

from django.utils.translation import gettext_lazy as _

# The add view uses BroadcastComposeForm, whose `title` and `message` are
# form-only fields (they are rendered into `context`) - so the add fieldsets
# cannot be the change ones.
ADD_FIELDSETS = (
    (None, {"fields": ("title", "message")}),
    (
        _("Audience"),
        {
            "fields": ("language", "require_device", "joined_after", "joined_before"),
            "description": _("Leave every filter unset to reach all active users."),
        },
    ),
    (_("Channels"), {"fields": ("channels",)}),
)

FIELDSETS = (
    (None, {"fields": ("kind", "context")}),
    (
        "Audience",
        {
            "fields": (
                "language",
                "require_device",
                "joined_after",
                "joined_before",
                "channels",
            )
        },
    ),
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
