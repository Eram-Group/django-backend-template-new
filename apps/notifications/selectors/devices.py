"""Reads for devices."""

import uuid
from collections.abc import Iterable

from apps.notifications.models import Device
from apps.users.models import User


def device_tokens_for_user(*, user: User) -> list[str]:
    return list(
        Device.objects.filter(user=user).values_list("registration_id", flat=True)
    )


def device_tokens_by_user_id(
    *, user_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Bulk counterpart for delivery batches: user id -> registration tokens."""
    tokens: dict[uuid.UUID, list[str]] = {}
    rows = Device.objects.filter(user_id__in=user_ids).values_list(
        "user_id", "registration_id"
    )
    for user_id, registration_id in rows:
        tokens.setdefault(user_id, []).append(registration_id)
    return tokens
