"""Liveness/readiness probes. URL wiring (/healthz, /readyz) lands in G03.

django-health-check imports stay inside the view so this module imports
without configured settings; the check backends themselves are registered
by the health_check.* entries in INSTALLED_APPS (G03 settings-base).
"""

from typing import Any

from django.http import HttpRequest
from django.http import JsonResponse


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. Must NOT touch the database or cache."""
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: database/cache/storage reachable (django-health-check)."""
    from health_check.mixins import CheckMixin

    probe: Any = CheckMixin()
    probe.check_all()
    failed = {
        plugin.identifier(): str(plugin.pretty_status())
        for plugin in probe.plugins
        if plugin.errors
    }
    if failed:
        return JsonResponse({"status": "unavailable", "failed": failed}, status=503)
    return JsonResponse({"status": "ok"})
