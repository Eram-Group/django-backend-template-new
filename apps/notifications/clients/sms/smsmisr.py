"""SMSMisr (smsmisr.com) - Egypt.

No bulk endpoint: ``send_many`` loops one POST per number. Broadcast-scale
Egyptian SMS therefore leans on worker parallelism (or a bulk-capable
provider later - a client-only change behind the SmsBackend protocol).
"""

import re
from collections.abc import Sequence

from django.conf import settings

from apps.common.http import request_json
from apps.notifications.clients.sms.base import SmsNotConfiguredError
from apps.notifications.clients.sms.base import SmsProviderError

_URL = "https://smsmisr.com/api/SMS/"
_SUCCESS_CODE = "1901"  # documented codes: 1903 bad creds, 1905 bad mobile,
# 1906 insufficient credit, 1910 bad language, 1912 bad environment, ...
_ARABIC_RE = re.compile(
    "[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]"
)


class SmsMisrBackend:
    """Sends via the SMSMisr v2 JSON API.

    ``environment`` (1=live, 2=test) comes from settings, not a hand flag;
    ``language`` is chosen PER MESSAGE from the body's script - the old
    template hardcoded Arabic and garbled English texts.
    """

    def send_many(self, *, to: Sequence[str], body: str) -> None:
        username = settings.SMSMISR_USERNAME
        password = settings.SMSMISR_PASSWORD
        sender = settings.SMSMISR_SENDER
        if username is None or password is None or sender is None:
            msg = "SMSMISR_USERNAME / SMSMISR_PASSWORD / SMSMISR_SENDER are not set"
            raise SmsNotConfiguredError(msg)
        for number in to:
            response = request_json(
                service="smsmisr",
                method="POST",
                url=_URL,
                json={
                    "environment": "1" if settings.SMSMISR_LIVE else "2",
                    "username": username,
                    "password": password.get_secret_value(),
                    "language": "2" if _ARABIC_RE.search(body) else "1",
                    "mobile": number.removeprefix("+"),
                    "sender": sender,
                    "message": body,
                },
                retry="transient",
            )
            payload = response.json()
            code = payload.get("code") if isinstance(payload, dict) else None
            if code != _SUCCESS_CODE:
                raise SmsProviderError(provider="smsmisr", detail=f"code={code!r}")
