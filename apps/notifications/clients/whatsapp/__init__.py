"""Outbound WhatsApp - resolves ``settings.WHATSAPP_BACKEND`` per call.

Console in base/local, MetaWhatsAppBackend (connector placeholder) when
deployed, Locmem in tests (``backends.outbox``). Same settings-string switch
as SMS_BACKEND / PUSH_BACKEND; per-call resolution keeps
``override_settings`` authoritative in tests.
"""

from collections.abc import Sequence

from django.conf import settings
from django.utils.module_loading import import_string

from apps.notifications.clients.whatsapp.base import WhatsAppBackend
from apps.notifications.clients.whatsapp.base import WhatsAppResult

__all__ = ["WhatsAppResult", "whatsapp_send_template"]


def whatsapp_send_template(
    *,
    to: str,
    template_name: str,
    language: str,
    variables: Sequence[str],
) -> WhatsAppResult:
    """Send one approved template; raises WhatsAppError subclasses on failure."""
    backend: WhatsAppBackend = import_string(settings.WHATSAPP_BACKEND)()
    return backend.send_template(
        to=to, template_name=template_name, language=language, variables=variables
    )
