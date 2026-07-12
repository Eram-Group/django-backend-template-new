"""Writes for devices (push-token lifecycle)."""

from apps.notifications.constants import DevicePlatform
from apps.notifications.models import Device
from apps.users.models import User


def device_register(
    *, user: User, registration_id: str, platform: DevicePlatform
) -> Device:
    """Idempotent upsert on registration_id.

    A token that shows up under a new account is REASSIGNED (one device =
    one signed-in user); platform updates ride along.
    """
    device = Device.objects.filter(registration_id=registration_id).first()
    if device is None:
        device = Device(registration_id=registration_id)
    device.user = user
    device.platform = platform
    device.full_clean()
    device.save()
    return device


def device_unregister(*, user: User, registration_id: str) -> None:
    """Delete the row (logout); scoped to the caller, idempotent."""
    Device.objects.filter(user=user, registration_id=registration_id).delete()
