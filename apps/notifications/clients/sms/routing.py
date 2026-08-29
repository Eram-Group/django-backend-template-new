"""The SMS transport: route numbers to providers by country.

One provider call per country group - OurSMS takes the whole group in a
single bulk POST; SMSMisr loops internally. Adding a provider = one module
implementing SmsBackend + one registry entry. There is no default provider:
a number whose region has no entry raises ``SmsProviderError`` BEFORE any
provider is called (the group fails, visibly, instead of silently riding an
international-capable provider nobody chose for it).

Providers run one after another, so a failure in the second group happens
AFTER the first group is already with its provider. Every failure raised past
the first accepted number therefore surfaces as ``SmsProviderError`` carrying
``sent`` - even a transport/config failure that would otherwise be systemic -
because escaping with the progress unrecorded would leave the accepted rows
PROCESSING for the sweep to re-send. A failure before anything went through
keeps its systemic type (the task fails loudly, nothing is lost).
"""

from collections.abc import Sequence

import phonenumbers

from apps.common.http import OutboundError
from apps.notifications.clients.sms.base import SmsBackend
from apps.notifications.clients.sms.base import SmsNotConfiguredError
from apps.notifications.clients.sms.base import SmsProviderError
from apps.notifications.clients.sms.oursms import OurSmsBackend
from apps.notifications.clients.sms.smsmisr import SmsMisrBackend

PROVIDER_REGISTRY: dict[str, type[SmsBackend]] = {
    "SA": OurSmsBackend,
    "EG": SmsMisrBackend,
}
ROUTER = "routing"  # ``SmsProviderError.provider`` for failures raised here


def provider_for(number: str) -> type[SmsBackend]:
    """The registry entry for the number's region; raises for anything else."""
    try:
        region = phonenumbers.region_code_for_number(phonenumbers.parse(number))
    except phonenumbers.NumberParseException as exc:
        raise SmsProviderError(
            provider=ROUTER, detail=f"unparseable number {number!r}: {exc}"
        ) from exc
    if region is None or region not in PROVIDER_REGISTRY:
        raise SmsProviderError(
            provider=ROUTER, detail=f"no SMS provider for region {region!r} ({number})"
        )
    return PROVIDER_REGISTRY[region]


class RoutingSmsBackend:
    def send_many(self, *, to: Sequence[str], body: str) -> None:
        groups: dict[type[SmsBackend], list[str]] = {}
        for number in to:
            groups.setdefault(provider_for(number), []).append(number)
        sent: list[str] = []
        for provider_cls, numbers in groups.items():
            try:
                provider_cls().send_many(to=numbers, body=body)
            except SmsProviderError as exc:
                raise SmsProviderError(
                    provider=exc.provider, detail=exc.detail, sent=[*sent, *exc.sent]
                ) from exc
            except (SmsNotConfiguredError, OutboundError) as exc:
                if not sent:
                    raise
                raise SmsProviderError(
                    provider=provider_cls.__name__, detail=str(exc), sent=sent
                ) from exc
            sent.extend(numbers)
