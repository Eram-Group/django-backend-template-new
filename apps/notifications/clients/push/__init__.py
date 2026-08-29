"""Outbound push - one transport: FcmPushBackend (firebase-admin, HTTP v1).

``_backend`` is the single seam: the notifications test conftest swaps it for
an in-memory outbox (``apps.notifications.tests.locmem``). No settings switch,
no console fallback - without FIREBASE_CREDENTIALS_B64 a send raises
PushNotConfiguredError.
"""

from collections.abc import Sequence

from apps.notifications.clients.push.base import PushBackend
from apps.notifications.clients.push.base import PushMessage
from apps.notifications.clients.push.base import PushResult
from apps.notifications.clients.push.fcm import FcmPushBackend

__all__ = ["PushMessage", "PushResult", "push_send_many"]


def _backend() -> PushBackend:
    return FcmPushBackend()


def push_send_many(*, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
    """Send one rendered message per token; results align with the input."""
    return _backend().send_many(messages=messages)
