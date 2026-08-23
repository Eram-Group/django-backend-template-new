"""Outbound push - ``push_send`` resolves ``settings.PUSH_BACKEND`` per call.

Console in base/local, FcmPushBackend (firebase-admin, HTTP v1) when
deployed, Locmem in tests (``backends.outbox``). Same settings-string switch
as SMS_BACKEND / Django's own mail backend.
"""

from collections.abc import Mapping
from collections.abc import Sequence

from django.conf import settings
from django.utils.module_loading import import_string

from apps.notifications.clients.push.base import PushBackend
from apps.notifications.clients.push.base import PushReport


def push_send(
    *,
    tokens: Sequence[str],
    title: str,
    body: str,
    data: Mapping[str, str],
) -> PushReport:
    """Multicast one message; raises PushError subclasses on failure."""
    backend: PushBackend = import_string(settings.PUSH_BACKEND)()
    return backend.send(tokens=tokens, title=title, body=body, data=data)
