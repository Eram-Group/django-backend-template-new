"""Liveness/readiness probes, served by ``HealthProbeMiddleware`` before host
and TLS enforcement (``/healthz``, ``/readyz``)."""

import uuid

import structlog
from django.core.cache import cache
from django.db import connection
from django.http import HttpRequest
from django.http import JsonResponse

logger = structlog.get_logger(__name__)


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. Must NOT touch the database or cache."""
    return JsonResponse({"status": "ok"})


def _database_ready() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _cache_ready() -> None:
    key = f"readyz:{uuid.uuid4()}"
    cache.set(key, "ok", 5)
    if cache.get(key) != "ok":
        msg = "cache round-trip failed"
        raise RuntimeError(msg)
    cache.delete(key)


_CHECKS = {"database": _database_ready, "cache": _cache_ready}


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: database and cache reachable.

    Deliberately NOT storage: a save/read/delete round-trip against S3 on a
    frequent LB probe is real cost and flaps the task out of rotation on any
    transient S3 blip. Storage health belongs in monitoring, not in the
    serve-traffic gate.
    """
    failed: dict[str, str] = {}
    for name, check in _CHECKS.items():
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - every driver error is "not ready"
            failed[name] = f"{type(exc).__name__}: {exc}"
    if failed:
        # The probe answers before ALLOWED_HOSTS and without auth: only the
        # check names go on the wire, the driver errors go to the log.
        logger.warning("readiness_failed", checks=failed)
        return JsonResponse(
            {"status": "unavailable", "failed": sorted(failed)}, status=503
        )
    return JsonResponse({"status": "ok"})
