from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.notifications.admin.device.resource import DeviceResource
from apps.notifications.models import Device


@admin.register(Device)
class DeviceAdmin(BaseModelAdmin):
    resource_classes = [DeviceResource]
