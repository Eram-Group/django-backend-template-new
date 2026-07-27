"""WhatsApp client: template capture, backend switch, placeholder guard."""

import pytest

from apps.notifications.clients.whatsapp import backends as whatsapp_backends
from apps.notifications.clients.whatsapp import whatsapp_send_template
from apps.notifications.clients.whatsapp.backends import ConsoleWhatsAppBackend
from apps.notifications.clients.whatsapp.backends import SentWhatsApp
from apps.notifications.clients.whatsapp.base import WhatsAppNotConfiguredError
from apps.notifications.clients.whatsapp.meta import MetaWhatsAppBackend


def test_whatsapp_send_template_uses_locmem_backend_in_tests() -> None:
    result = whatsapp_send_template(
        to="+966501234567",
        template_name="announcement",
        language="ar",
        variables=["first", "second"],
    )

    assert result.message_id == "locmem-1"
    assert whatsapp_backends.outbox == [
        SentWhatsApp(
            to="+966501234567",
            template_name="announcement",
            language="ar",
            variables=("first", "second"),
        )
    ]


def test_locmem_message_ids_are_deterministic() -> None:
    first = whatsapp_send_template(
        to="+966501234567", template_name="a", language="en", variables=[]
    )
    second = whatsapp_send_template(
        to="+966501234568", template_name="a", language="en", variables=[]
    )

    assert (first.message_id, second.message_id) == ("locmem-1", "locmem-2")


def test_console_backend_returns_a_message_id() -> None:
    result = ConsoleWhatsAppBackend().send_template(
        to="+966501234567", template_name="a", language="en", variables=[]
    )

    assert result.message_id.startswith("console-")


def test_meta_placeholder_is_loud() -> None:
    """Until the connector PR lands, a real send must fail, never no-op."""
    with pytest.raises(WhatsAppNotConfiguredError):
        MetaWhatsAppBackend().send_template(
            to="+966501234567", template_name="a", language="en", variables=[]
        )
