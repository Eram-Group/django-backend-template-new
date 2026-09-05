"""Import-export resources for notifications: one per admin, explicit fields only.

Exports are read by non-engineers - never raw provider payloads or credentials.
"""

from apps.common.admin import BaseModelResource
from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig


class BroadcastResource(BaseModelResource):
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
        model = Broadcast
        fields = ("id", "created_at")


class DeviceResource(BaseModelResource):
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
        model = Device
        fields = ("id", "created_at")


class NotificationResource(BaseModelResource):
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
        model = Notification
        fields = ("id", "created_at")


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
