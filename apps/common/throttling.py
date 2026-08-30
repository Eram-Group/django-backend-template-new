"""Throttles for the ninja API, keyed on the authenticated principal.

ninja's ``AuthRateThrottle`` keys on ``str(request.auth)`` and pins one cache
scope for every instance, so two of them (an API-wide ceiling and a tighter
per-endpoint one) would share a single counter. This one keys on the
principal's pk and takes its scope per instance. Anonymous requests reach
a throttle only on the few public routes (``auth=None``, e.g. the country
list); there the key falls back to the client IP (``get_ident``).
"""

from django.http import HttpRequest
from ninja.throttling import SimpleRateThrottle


class PrincipalRateThrottle(SimpleRateThrottle):
    def __init__(self, rate: str, *, scope: str) -> None:
        self.scope = scope
        super().__init__(rate)

    def get_cache_key(self, request: HttpRequest) -> str:
        principal = getattr(request, "auth", None)
        ident = str(principal.pk) if principal is not None else self.get_ident(request)
        return self.cache_format % {"scope": self.scope, "ident": ident}
