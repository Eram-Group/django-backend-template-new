"""Outbound push - ``push_send_many`` resolves ``settings.PUSH_BACKEND`` per call.

Console in base/local, FcmPushBackend (firebase-admin, HTTP v1) when
deployed, Locmem in tests (``backends.outbox``). Same settings-string switch
as SMS_BACKEND / EMAIL_BACKEND; per-call resolution keeps
``override_settings`` authoritative in tests.
"""

from collections.abc import Sequence

from django.conf import settings
from django.utils.module_loading import import_string

from apps.notifications.clients.push.base import PushBackend
from apps.notifications.clients.push.base import PushMessage
from apps.notifications.clients.push.base import PushResult

__all__ = ["PushMessage", "PushResult", "push_send_many"]


def push_send_many(*, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
    """Send one rendered message per token; results align with the input."""
    backend: PushBackend = import_string(settings.PUSH_BACKEND)()
    return backend.send_many(messages=messages)
