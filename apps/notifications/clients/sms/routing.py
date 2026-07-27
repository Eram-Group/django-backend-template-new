"""The deployed SMS_BACKEND: route numbers to providers by country.

One provider call per country group - OurSMS takes the whole group in a
single bulk POST; SMSMisr loops internally. Adding a provider = one module
implementing SmsBackend + one registry entry.
"""

from collections.abc import Sequence

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
    def send_many(self, *, to: Sequence[str], body: str) -> None:
        groups: dict[type[SmsBackend], list[str]] = {}
        for number in to:
            region = phonenumbers.region_code_for_number(phonenumbers.parse(number))
            provider_cls = PROVIDER_REGISTRY.get(region or "", DEFAULT_PROVIDER)
            groups.setdefault(provider_cls, []).append(number)
        for provider_cls, numbers in groups.items():
            provider_cls().send_many(to=numbers, body=body)
