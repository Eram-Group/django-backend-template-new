"""The deployed SMS_BACKEND: route each number to a provider by country.

Adding a provider = one module implementing SmsBackend + one registry entry.
"""

import phonenumbers

from apps.notifications.clients.sms.base import SmsBackend
from apps.notifications.clients.sms.oursms import OurSmsBackend
from apps.notifications.clients.sms.smsmisr import SmsMisrBackend

PROVIDER_REGISTRY: dict[str, type[SmsBackend]] = {
    "SA": OurSmsBackend,
    "EG": SmsMisrBackend,
}
DEFAULT_PROVIDER: type[SmsBackend] = OurSmsBackend  # international-capable


class RoutingSmsBackend:
    def send(self, *, to: str, body: str) -> None:
        region = phonenumbers.region_code_for_number(phonenumbers.parse(to))
        provider_cls = PROVIDER_REGISTRY.get(region or "", DEFAULT_PROVIDER)
        provider_cls().send(to=to, body=body)
