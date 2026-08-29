"""Outbound WhatsApp - one transport: MetaWhatsAppBackend (Cloud API).

``_backend`` is the single seam: the notifications test conftest swaps it for
an in-memory outbox (``apps.notifications.tests.locmem``). No settings switch,
no console fallback - until the connector lands, a real send raises
WhatsAppNotConfiguredError.
"""

from collections.abc import Sequence

from apps.notifications.clients.whatsapp.base import WhatsAppBackend
from apps.notifications.clients.whatsapp.base import WhatsAppResult
from apps.notifications.clients.whatsapp.meta import MetaWhatsAppBackend

__all__ = ["WhatsAppResult", "whatsapp_send_template"]


def _backend() -> WhatsAppBackend:
    return MetaWhatsAppBackend()


def whatsapp_send_template(
    *,
    to: str,
    template_name: str,
    language: str,
    variables: Sequence[str],
) -> WhatsAppResult:
    """Send one approved template; raises WhatsAppError subclasses on failure."""
    return _backend().send_template(
        to=to, template_name=template_name, language=language, variables=variables
    )
