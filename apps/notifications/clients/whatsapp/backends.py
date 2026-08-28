"""Local/test WhatsApp transports: console (structlog) and in-memory outbox."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from apps.notifications.clients.whatsapp.base import WhatsAppResult

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SentWhatsApp:
    to: str
    template_name: str
    language: str
    variables: tuple[str, ...]


#: The ``mail.outbox`` analogue - LocmemWhatsAppBackend appends here; the
#: notifications test conftest clears it between tests.
outbox: list[SentWhatsApp] = []


class ConsoleWhatsAppBackend:
    """Logs instead of sending - the local-dev default."""

    def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: Sequence[str],
    ) -> WhatsAppResult:
        logger.info(
            "whatsapp_console_send",
            to=to,
            template_name=template_name,
            language=language,
            variables=list(variables),
        )
        return WhatsAppResult(message_id=f"console-{uuid.uuid4()}")


class LocmemWhatsAppBackend:
    def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: Sequence[str],
    ) -> WhatsAppResult:
        outbox.append(
            SentWhatsApp(
                to=to,
                template_name=template_name,
                language=language,
                variables=tuple(variables),
            )
        )
        return WhatsAppResult(message_id=f"locmem-{len(outbox)}")
