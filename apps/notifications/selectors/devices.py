"""Reads for devices."""

from apps.notifications.models import Device
from apps.users.models import User


def device_tokens_for_user(*, user: User) -> list[str]:
    return list(
        Device.objects.filter(user=user).values_list("registration_id", flat=True)
    )
