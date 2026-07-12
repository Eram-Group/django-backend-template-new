"""OurSMS (api.oursms.com) - Saudi Arabia, and the international default."""

from typing import Any

from django.conf import settings

from apps.common.http import request_json
from apps.notifications.clients.sms.base import SmsNotConfiguredError
from apps.notifications.clients.sms.base import SmsProviderError

_URL = "https://api.oursms.com/msgs/sms"


class OurSmsBackend:
    """Transactional sends via the OurSMS JSON API (Bearer key)."""

    def send(self, *, to: str, body: str) -> None:
        api_key = settings.OURSMS_API_KEY
        sender = settings.OURSMS_SENDER
        if api_key is None or sender is None:
            msg = "OURSMS_API_KEY / OURSMS_SENDER are not set"
            raise SmsNotConfiguredError(msg)
        response = request_json(
            service="oursms",
            method="POST",
            url=_URL,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            json={
                "src": sender,
                "dests": [to],
                "body": body,
                "priority": 1,
                "msgClass": "transactional",
            },
            retry="transient",
        )
        _assert_accepted(response.json())


def _assert_accepted(payload: Any) -> None:
    """Allowlist success predicate: unknown response shapes are failures.

    Pinned to the OurSMS v1 contract (accepted/rejected counts). If your
    account's API version answers differently, the first real send fails
    loudly with the body in the error - adjust HERE, never by trusting a
    bare 2xx (the old template counted error bodies as delivered).
    """
    if (
        isinstance(payload, dict)
        and payload.get("accepted", 0) >= 1
        and payload.get("rejected", 0) == 0
    ):
        return
    raise SmsProviderError(provider="oursms", detail=f"unexpected response: {payload}")
