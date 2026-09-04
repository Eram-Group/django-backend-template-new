"""Probe paths must answer a raw load-balancer check.

The ALB sends ``Host: <task-ip>:8000`` over plain http; without the
short-circuit middleware, SecurityMiddleware redirects (301) or host
validation rejects (400) and the task never becomes healthy.
"""

import pytest
from django.test import Client
from django.test import override_settings

PROBE_SETTINGS = {"ALLOWED_HOSTS": ["example.com"], "SECURE_SSL_REDIRECT": True}


@override_settings(**PROBE_SETTINGS)
def test_healthz_bypasses_host_and_tls_checks(client: Client) -> None:
    response = client.get("/healthz", HTTP_HOST="10.0.0.1:8000")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@override_settings(**PROBE_SETTINGS)
@pytest.mark.django_db
def test_readyz_bypasses_host_and_tls_checks(client: Client) -> None:
    response = client.get("/readyz", HTTP_HOST="10.0.0.1:8000")
    assert response.status_code == 200


@override_settings(**PROBE_SETTINGS)
def test_probe_paths_reject_other_methods(client: Client) -> None:
    response = client.post("/healthz", HTTP_HOST="10.0.0.1:8000")
    assert response.status_code == 405


@override_settings(**PROBE_SETTINGS)
def test_other_paths_keep_host_validation(client: Client) -> None:
    response = client.get("/", HTTP_HOST="10.0.0.1:8000")
    assert response.status_code == 400


@pytest.mark.django_db
def test_readyz_reports_the_failing_check_by_name_only(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """503 names the check, never the driver error (the probe answers before
    host validation and without auth)."""
    from apps.common import health

    def broken() -> None:
        msg = "password authentication failed for user postgres"
        raise RuntimeError(msg)

    monkeypatch.setitem(health._CHECKS, "database", broken)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "failed": ["database"]}
    assert "postgres" not in response.content.decode()
