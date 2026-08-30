"""flagcdn.com - PNG flags by lowercase alpha-2 code (no auth, no quota)."""

from apps.common.http import PROVIDER_TIMEOUT
from apps.common.http import request_json

FLAG_URL = "https://flagcdn.com/w320/{code}.png"


def flag_fetch(*, code: str) -> bytes:
    """The 320px-wide PNG for ``code``. Raises OutboundError on failure."""
    response = request_json(
        service="flagcdn",
        method="GET",
        url=FLAG_URL.format(code=code.lower()),
        timeout=PROVIDER_TIMEOUT,
        retry="transient",
    )
    return response.content
