"""In-memory SMS/push/WhatsApp transports - the ``mail.outbox`` analogue.

The ``outboxes`` autouse fixture (conftest.py) points each client's
``_backend`` seam at these and empties the lists before every test. Nothing
outside the test suite imports this module.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from apps.notifications.clients.push.base import PushMessage
from apps.notifications.clients.push.base import PushResult
from apps.notifications.clients.whatsapp.base import WhatsAppResult


@dataclass(frozen=True, slots=True)
class SentSms:
    to: str
    body: str


@dataclass(frozen=True, slots=True)
class SentWhatsApp:
    to: str
    template_name: str
    language: str
    variables: tuple[str, ...]


#: One entry PER RECIPIENT.
sms_outbox: list[SentSms] = []
#: One entry PER MESSAGE (= per device token).
push_outbox: list[PushMessage] = []
#: One entry per template send.
whatsapp_outbox: list[SentWhatsApp] = []


class LocmemSmsBackend:
    def send_many(self, *, to: Sequence[str], body: str) -> None:
        sms_outbox.extend(SentSms(to=number, body=body) for number in to)


class LocmemPushBackend:
    def send_many(self, *, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
        push_outbox.extend(messages)
        return tuple(PushResult(token=m.token, ok=True) for m in messages)


class LocmemWhatsAppBackend:
    def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: Sequence[str],
    ) -> WhatsAppResult:
        whatsapp_outbox.append(
            SentWhatsApp(
                to=to,
                template_name=template_name,
                language=language,
                variables=tuple(variables),
            )
        )
        return WhatsAppResult(message_id=f"locmem-{len(whatsapp_outbox)}")
