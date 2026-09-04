from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.notifications.admin.notificationdelivery.resource import (
    NotificationDeliveryResource,
)
from apps.notifications.models import NotificationDelivery


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(BaseModelAdmin):
    resource_classes = [NotificationDeliveryResource]
