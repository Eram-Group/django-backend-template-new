from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.notifications.admin.notification.resource import NotificationResource
from apps.notifications.admin.notificationdelivery.inline import (
    NotificationDeliveryInline,
)
from apps.notifications.models import Notification


@admin.register(Notification)
class NotificationAdmin(BaseModelAdmin):
    resource_classes = [NotificationResource]

    inlines = [NotificationDeliveryInline]
