"""Local/test SMS transports: console (structlog) and in-memory outbox."""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SentSms:
    to: str
    body: str


#: The ``mail.outbox`` analogue - LocmemSmsBackend appends here; the
#: notifications test conftest clears it between tests.
outbox: list[SentSms] = []


class ConsoleSmsBackend:
    """Logs instead of sending - the local-dev default (Mailpit's role for email)."""

    def send(self, *, to: str, body: str) -> None:
        logger.info("sms_console_send", to=to, body=body)


class LocmemSmsBackend:
    def send(self, *, to: str, body: str) -> None:
        outbox.append(SentSms(to=to, body=body))
