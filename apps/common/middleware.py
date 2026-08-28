"""Health-probe short-circuit for load-balancer checks.

ALB / ECS health checks arrive as plain HTTP with the task IP as ``Host``
and no ``X-Forwarded-Proto``. Left to the normal stack, SecurityMiddleware
answers 301 (SECURE_SSL_REDIRECT) and host validation answers 400
(ALLOWED_HOSTS cannot express an IP range), so the target never turns
healthy. This middleware sits FIRST in MIDDLEWARE and serves the two probe
paths directly; every other request passes through untouched.
"""

from collections.abc import Callable

from django.http import HttpRequest
from django.http import HttpResponse

from apps.common.health import healthz
from apps.common.health import readyz

_PROBES: dict[str, Callable[[HttpRequest], HttpResponse]] = {
    "/healthz": healthz,
    "/readyz": readyz,
}


class HealthProbeMiddleware:
    """Serve ``/healthz`` and ``/readyz`` before host/TLS enforcement."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        probe = _PROBES.get(request.path)
        if probe is not None and request.method in {"GET", "HEAD"}:
            return probe(request)
        return self.get_response(request)
