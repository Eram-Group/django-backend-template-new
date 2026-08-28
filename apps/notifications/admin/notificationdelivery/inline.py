"""Read-only delivery rows under the Notification change form."""

from apps.common.admin import ReadOnlyTabularInline
from apps.notifications.models import NotificationDelivery


class NotificationDeliveryInline(ReadOnlyTabularInline):
    """Per-channel outcome of the parent notification."""

    model = NotificationDelivery
    fields = (
        "channel",
        "status",
        "provider",
        "provider_message_id",
        "attempts",
        "sent_at",
        "detail",
    )
