"""Import-export resource for NotificationKindConfig (explicit fields only)."""

from apps.common.admin import BaseModelResource
from apps.notifications.models import NotificationKindConfig


class NotificationKindConfigResource(BaseModelResource):
    class Meta:
        model = NotificationKindConfig
        fields = (
            "id",
            "kind",
            "channels",
            "title_ar",
            "title_en",
            "body_ar",
            "body_en",
        )
