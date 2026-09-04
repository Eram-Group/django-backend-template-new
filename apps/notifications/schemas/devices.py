import uuid
from datetime import datetime

from ninja import Field
from ninja import Schema

from apps.notifications.constants import REGISTRATION_ID_MAX_LENGTH
from apps.notifications.constants import DevicePlatform


class DeviceRegisterIn(Schema):
    registration_id: str = Field(min_length=1, max_length=REGISTRATION_ID_MAX_LENGTH)
    platform: DevicePlatform


class DeviceDetail(Schema):
    id: uuid.UUID
    registration_id: str
    platform: DevicePlatform
    created_at: datetime
