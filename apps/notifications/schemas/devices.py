import uuid
from datetime import datetime

from ninja import Schema

from apps.notifications.constants import DevicePlatform


class DeviceRegisterIn(Schema):
    registration_id: str
    platform: DevicePlatform


class DeviceDetail(Schema):
    id: uuid.UUID
    registration_id: str
    platform: DevicePlatform
    created_at: datetime
