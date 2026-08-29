"""WhatsApp client: the swapped seam captures templates; Meta stays loud."""

import pytest

from apps.notifications.clients.whatsapp import whatsapp_send_template
from apps.notifications.clients.whatsapp.base import WhatsAppNotConfiguredError
from apps.notifications.clients.whatsapp.meta import MetaWhatsAppBackend
from apps.notifications.tests.locmem import SentWhatsApp
from apps.notifications.tests.locmem import whatsapp_outbox


def test_whatsapp_send_template_goes_through_the_swapped_seam() -> None:
    result = whatsapp_send_template(
        to="+966501234567",
        template_name="announcement",
        language="ar",
        variables=["first", "second"],
    )

    assert result.message_id == "locmem-1"
    assert whatsapp_outbox == [
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


def test_meta_placeholder_is_loud() -> None:
    """Until the connector PR lands, a real send must fail, never no-op."""
    with pytest.raises(WhatsAppNotConfiguredError):
        MetaWhatsAppBackend().send_template(
            to="+966501234567", template_name="a", language="en", variables=[]
        )
