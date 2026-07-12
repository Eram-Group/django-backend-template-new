"""FCM via firebase-admin (HTTP v1 API).

firebase-admin transports its own HTTP (auth, batching, backoff) - the
apps.common.http kernel is deliberately not involved here. We use the typed
``send_each`` API (one Message per token, 500 per call); tokens Firebase
reports as unregistered come back in PushReport.invalid_tokens so the caller
prunes their Device rows - the modern replacement for fcm-django's
DELETE_INACTIVE_DEVICES (fcm-django itself hard-depends on DRF; this repo is
ninja-only).
"""

import base64
import functools
import itertools
import json
from collections.abc import Mapping
from collections.abc import Sequence
from typing import TYPE_CHECKING

from django.conf import settings

from apps.notifications.clients.push.base import PushNotConfiguredError
from apps.notifications.clients.push.base import PushReport

if TYPE_CHECKING:
    import firebase_admin

_BATCH_LIMIT = 500  # FCM's per-call ceiling for send_each


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
    def send(
        self,
        *,
        tokens: Sequence[str],
        title: str,
        body: str,
        data: Mapping[str, str],
    ) -> PushReport:
        from firebase_admin import messaging

        app = _firebase_app()
        sent = failed = 0
        invalid: list[str] = []
        for chunk in itertools.batched(tokens, _BATCH_LIMIT, strict=False):
            messages = [
                messaging.Message(
                    notification=messaging.Notification(title=title, body=body),
                    data=dict(data),
                    token=token,
                )
                for token in chunk
            ]
            batch = messaging.send_each(messages, app=app)
            for token, result in zip(chunk, batch.responses, strict=True):
                if result.success:
                    sent += 1
                else:
                    failed += 1
                    if isinstance(result.exception, messaging.UnregisteredError):
                        invalid.append(token)
        return PushReport(sent=sent, failed=failed, invalid_tokens=tuple(invalid))
