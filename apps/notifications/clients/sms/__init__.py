"""Outbound SMS - one transport: RoutingSmsBackend (OurSMS/SMSMisr by country).

``_backend`` is the single seam: the notifications test conftest swaps it for
an in-memory outbox (``apps.notifications.tests.locmem``) so the suite never
touches provider HTTP. There is no settings switch and no console fallback -
an environment without provider credentials fails with SmsNotConfiguredError.
"""

from collections.abc import Sequence

from apps.notifications.clients.sms.base import SmsBackend
from apps.notifications.clients.sms.routing import RoutingSmsBackend

__all__ = ["sms_send_many"]


def _backend() -> SmsBackend:
    return RoutingSmsBackend()


def sms_send_many(*, to: Sequence[str], body: str) -> None:
    """Deliver ONE body to MANY E164 numbers; raises SmsError on failure."""
    _backend().send_many(to=to, body=body)
