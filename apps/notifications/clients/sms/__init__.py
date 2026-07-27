"""Outbound SMS - ``sms_send_many`` resolves ``settings.SMS_BACKEND`` per call.

The settings-string switch mirrors EMAIL_BACKEND: Console in base/local,
RoutingSmsBackend (OurSMS/SMSMisr by phone country) when deployed, Locmem in
tests (``backends.outbox`` is the ``mail.outbox`` analogue). Per-call
resolution keeps ``override_settings`` authoritative in tests.
"""

from collections.abc import Sequence

from django.conf import settings
from django.utils.module_loading import import_string

from apps.notifications.clients.sms.base import SmsBackend

__all__ = ["sms_send", "sms_send_many"]


def sms_send_many(*, to: Sequence[str], body: str) -> None:
    """Deliver ONE body to MANY E164 numbers; raises SmsError on failure."""
    backend: SmsBackend = import_string(settings.SMS_BACKEND)()
    backend.send_many(to=to, body=body)


def sms_send(*, to: str, body: str) -> None:
    """Deliver one SMS to one E164 number; raises SmsError on failure."""
    sms_send_many(to=[to], body=body)
