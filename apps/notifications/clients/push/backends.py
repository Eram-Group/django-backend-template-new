"""Local/test push transports: console (structlog) and in-memory outbox."""

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from apps.notifications.clients.push.base import PushReport

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SentPush:
    tokens: tuple[str, ...]
    title: str
    body: str
    data: Mapping[str, str]


#: The ``mail.outbox`` analogue - LocmemPushBackend appends here; the
#: notifications test conftest clears it between tests.
outbox: list[SentPush] = []


class ConsolePushBackend:
    """Logs instead of sending - the local-dev default."""

    def send(
        self,
        *,
        tokens: Sequence[str],
        title: str,
        body: str,
        data: Mapping[str, str],
    ) -> PushReport:
        logger.info(
            "push_console_send", tokens=list(tokens), title=title, body=body, data=data
        )
        return PushReport(sent=len(tokens), failed=0)


class LocmemPushBackend:
    def send(
        self,
        *,
        tokens: Sequence[str],
        title: str,
        body: str,
        data: Mapping[str, str],
    ) -> PushReport:
        outbox.append(
            SentPush(tokens=tuple(tokens), title=title, body=body, data=dict(data))
        )
        return PushReport(sent=len(tokens), failed=0)
