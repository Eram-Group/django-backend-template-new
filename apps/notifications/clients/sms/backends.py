"""Local/test SMS transports: console (structlog) and in-memory outbox."""

from collections.abc import Sequence
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SentSms:
    to: str
    body: str


#: The ``mail.outbox`` analogue - LocmemSmsBackend appends one entry PER
#: RECIPIENT; the notifications test conftest clears it between tests.
outbox: list[SentSms] = []


class ConsoleSmsBackend:
    """Logs instead of sending - the local-dev default (Mailpit's role for email)."""

    def send_many(self, *, to: Sequence[str], body: str) -> None:
        logger.info("sms_console_send", to=list(to), body=body)


class LocmemSmsBackend:
    def send_many(self, *, to: Sequence[str], body: str) -> None:
        outbox.extend(SentSms(to=number, body=body) for number in to)
