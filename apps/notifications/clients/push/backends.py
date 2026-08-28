"""Local/test push transports: console (structlog) and in-memory outbox."""

from collections.abc import Sequence

import structlog

from apps.notifications.clients.push.base import PushMessage
from apps.notifications.clients.push.base import PushResult

logger = structlog.get_logger(__name__)

#: The ``mail.outbox`` analogue - LocmemPushBackend appends one entry PER
#: MESSAGE; the notifications test conftest clears it between tests.
outbox: list[PushMessage] = []


class ConsolePushBackend:
    """Logs instead of sending - the local-dev default."""

    def send_many(self, *, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
        for message in messages:
            logger.info(
                "push_console_send",
                token=message.token,
                title=message.title,
                body=message.body,
                data=dict(message.data),
            )
        return tuple(PushResult(token=m.token, ok=True) for m in messages)


class LocmemPushBackend:
    def send_many(self, *, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
        outbox.extend(messages)
        return tuple(PushResult(token=m.token, ok=True) for m in messages)
