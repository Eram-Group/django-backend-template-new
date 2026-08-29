"""Read-only delivery rows under the Notification change form."""

from apps.common.admin import BaseTabularInline
from apps.notifications.models import NotificationDelivery


class NotificationDeliveryInline(BaseTabularInline):
    """Per-channel outcome of the parent notification: display and
    navigation (show_change_link) only."""

    model = NotificationDelivery
    can_add = False
    can_change = False
    can_delete = False
    fields = (
        "channel",
        "status",
        "provider",
        "provider_message_id",
        "attempts",
        "sent_at",
        "detail",
    )
