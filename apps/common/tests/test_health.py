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
def test_probe_paths_only_answer_get(client: Client) -> None:
    # A POST to a probe path is not a probe - it falls through to the normal
    # stack, which rejects the unknown host.
    response = client.post("/healthz", HTTP_HOST="10.0.0.1:8000")
    assert response.status_code == 400


@override_settings(**PROBE_SETTINGS)
def test_other_paths_keep_host_validation(client: Client) -> None:
    response = client.get("/", HTTP_HOST="10.0.0.1:8000")
    assert response.status_code == 400
