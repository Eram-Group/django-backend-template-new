"""Liveness/readiness probes. URL wiring (/healthz, /readyz) lands in G03.

django-health-check 4.x is used as a plain library (dataclass checks run
on demand) - it needs no INSTALLED_APPS entries. Imports stay inside the
view so the module imports without configured settings.
"""

import asyncio

import structlog
from django.http import HttpRequest
from django.http import JsonResponse

logger = structlog.get_logger(__name__)


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. Must NOT touch the database or cache."""
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: database and cache reachable (django-health-check).

    Deliberately NOT storage: the Storage check does a save/read/delete
    round-trip against S3, which on a frequent LB probe is real cost and
    flaps the pod out of rotation on any transient S3 blip. Storage health
    belongs in monitoring, not in the serve-traffic gate.
    """
    from health_check.base import HealthCheckResult
    from health_check.checks import Cache
    from health_check.checks import Database

    async def run_all() -> list[HealthCheckResult]:
        checks = (Database(), Cache())
        return [await check.get_result() for check in checks]

    results = asyncio.run(run_all())
    failed = {
        type(result.check).__name__: str(result.error)
        for result in results
        if result.error
    }
    if failed:
        # The probe answers before ALLOWED_HOSTS and without auth: only the
        # check names go on the wire, the driver errors go to the log.
        logger.warning("readiness_failed", checks=failed)
        return JsonResponse(
            {"status": "unavailable", "failed": sorted(failed)}, status=503
        )
    return JsonResponse({"status": "ok"})
