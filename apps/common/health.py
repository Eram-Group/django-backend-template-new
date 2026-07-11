"""Liveness/readiness probes. URL wiring (/healthz, /readyz) lands in G03.

django-health-check 4.x is used as a plain library (dataclass checks run
on demand) - it needs no INSTALLED_APPS entries. Imports stay inside the
view so the module imports without configured settings.
"""

import asyncio

from django.http import HttpRequest
from django.http import JsonResponse


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. Must NOT touch the database or cache."""
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: database/cache/storage reachable (django-health-check)."""
    from health_check.base import HealthCheckResult
    from health_check.checks import Cache
    from health_check.checks import Database
    from health_check.checks import Storage

    async def run_all() -> list[HealthCheckResult]:
        checks = (Database(), Cache(), Storage())
        return [await check.get_result() for check in checks]

    results = asyncio.run(run_all())
    failed = {
        repr(result.check): str(result.error) for result in results if result.error
    }
    if failed:
        return JsonResponse({"status": "unavailable", "failed": failed}, status=503)
    return JsonResponse({"status": "ok"})
