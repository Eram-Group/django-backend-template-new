"""OurSMS (api.oursms.com) - Saudi Arabia, and the international default.

Natively bulk: one POST carries the whole ``dests`` group (callers group by
rendered body first, so one call = one body, many numbers). The per-call
recipient ceiling is undocumented - delivery batches cap groups at ~200,
well within any sane limit; confirm with the provider before raising it.
"""

from collections.abc import Sequence
from typing import Any

from django.conf import settings

from apps.common.http import PROVIDER_TIMEOUT
from apps.common.http import request_json
from apps.notifications.clients.sms.base import SmsNotConfiguredError
from apps.notifications.clients.sms.base import SmsProviderError

_URL = "https://api.oursms.com/msgs/sms"


class OurSmsBackend:
    """Transactional sends via the OurSMS JSON API (Bearer key)."""

    def send_many(self, *, to: Sequence[str], body: str) -> None:
        api_key = settings.OURSMS_API_KEY
        sender = settings.OURSMS_SENDER
        if api_key is None or sender is None:
            msg = "OURSMS_API_KEY / OURSMS_SENDER are not set"
            raise SmsNotConfiguredError(msg)
        if not to:
            return
        response = request_json(
            service="oursms",
            method="POST",
            url=_URL,
            headers={"Authorization": f"Bearer {api_key.get_secret_value()}"},
            json={
                "src": sender,
                "dests": list(to),
                "body": body,
                "priority": 1,
                "msgClass": "transactional",
            },
            timeout=PROVIDER_TIMEOUT,
            retry="transient",
        )
        _assert_accepted(response.json(), expected=len(to))


def _assert_accepted(payload: Any, *, expected: int) -> None:
    """Allowlist success predicate: unknown response shapes are failures.

    Pinned to the OurSMS v1 contract (accepted/rejected counts). Every
    recipient must be accepted - a partial acceptance fails the whole group
    (counts carry no per-number detail). If your account's API version
    answers differently, the first real send fails loudly with the body in
    the error - adjust HERE, never by trusting a bare 2xx (the old template
    counted error bodies as delivered).
    """
    if (
        isinstance(payload, dict)
        and payload.get("accepted", 0) == expected
        and payload.get("rejected", 0) == 0
    ):
        return
    raise SmsProviderError(provider="oursms", detail=f"unexpected response: {payload}")
