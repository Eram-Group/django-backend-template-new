"""Import-export resource for NotificationDelivery (explicit fields only)."""

from apps.common.admin import BaseModelResource
from apps.notifications.models import NotificationDelivery


class NotificationDeliveryResource(BaseModelResource):
    # Exports are read by non-engineers - rename columns and format dates:
    #   from import_export.fields import Field
    #   from import_export.widgets import DateTimeWidget
    #
    #   created_at = Field(
    #       attribute="created_at",
    #       column_name="Created At",
    #       widget=DateTimeWidget(format="%Y-%m-%d %H:%M:%S"),
    #   )
    class Meta:
        model = NotificationDelivery
        fields = ("id", "created_at")
