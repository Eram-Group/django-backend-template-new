"""Writes for devices (push-token lifecycle)."""

from apps.notifications.constants import DevicePlatform
from apps.notifications.models import Device
from apps.users.models import User


def device_register(
    *, user: User, registration_id: str, platform: DevicePlatform
) -> Device:
    """Idempotent upsert on registration_id (update_or_create).

    A token that shows up under a new account is REASSIGNED (one device =
    one signed-in user); platform updates ride along.
    """
    device, _created = Device.objects.update_or_create(
        registration_id=registration_id,
        defaults={"user": user, "platform": platform},
    )
    # update_or_create saves before validation can run; full_clean after
    # still protects - the caller's transaction (ATOMIC_REQUESTS) discards
    # the write when this raises.
    device.full_clean()
    return device


def device_unregister(*, user: User, registration_id: str) -> None:
    """Delete the row (logout); scoped to the caller, idempotent."""
    Device.objects.filter(user=user, registration_id=registration_id).delete()
