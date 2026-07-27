"""FCM via firebase-admin (HTTP v1 API).

firebase-admin transports its own HTTP (auth, backoff) - the apps.common.http
kernel is deliberately not involved here. ``send_each`` is NOT a batch call:
it opens one thread + one HTTP request per message (hard cap 500 per call),
so we chunk at 200 to bound thread fan-out. Tokens Firebase reports as dead
(unregistered / sender-id mismatch) come back with ``invalid=True`` so the
caller prunes their Device rows - transient failures never set it.
"""

import base64
import functools
import itertools
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.conf import settings

from apps.notifications.clients.push.base import PushMessage
from apps.notifications.clients.push.base import PushNotConfiguredError
from apps.notifications.clients.push.base import PushResult

if TYPE_CHECKING:
    import firebase_admin

_BATCH_LIMIT = 200  # send_each spawns a thread per message; 500 is its hard cap


@functools.cache
def _firebase_app() -> firebase_admin.App:
    """Lazy init from the base64 service-account JSON (never at import time)."""
    import firebase_admin
    from firebase_admin import credentials

    raw = settings.FIREBASE_CREDENTIALS_B64
    if raw is None:
        msg = "FIREBASE_CREDENTIALS_B64 is not set"
        raise PushNotConfiguredError(msg)
    info = json.loads(base64.b64decode(raw.get_secret_value()))
    return firebase_admin.initialize_app(credentials.Certificate(info))


class FcmPushBackend:
    def send_many(self, *, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
        from firebase_admin import messaging

        app = _firebase_app()
        results: list[PushResult] = []
        for chunk in itertools.batched(messages, _BATCH_LIMIT, strict=False):
            fcm_messages = [
                messaging.Message(
                    notification=messaging.Notification(
                        title=message.title, body=message.body
                    ),
                    data=dict(message.data),
                    token=message.token,
                )
                for message in chunk
            ]
            batch = messaging.send_each(fcm_messages, app=app)
            for message, result in zip(chunk, batch.responses, strict=True):
                if result.success:
                    results.append(PushResult(token=message.token, ok=True))
                else:
                    invalid = isinstance(
                        result.exception,
                        messaging.UnregisteredError | messaging.SenderIdMismatchError,
                    )
                    results.append(
                        PushResult(
                            token=message.token,
                            ok=False,
                            invalid=invalid,
                            detail=type(result.exception).__name__,
                        )
                    )
        return tuple(results)
