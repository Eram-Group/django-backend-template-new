"""Envelope contract: ApplicationError responses carry extra['code'].

A probe NinjaAPI (this module doubles as its urlconf via pytest.mark.urls)
routes a real request through config/api/exception_handlers.py - messages
are localized, so the stable code is what clients branch on.
"""

import pytest
from django.http import HttpRequest
from django.test import Client
from django.urls import path
from ninja import NinjaAPI

from apps.common.exceptions import ApplicationError
from config.api.exception_handlers import register_exception_handlers


class TeapotRefusedError(ApplicationError):
    status_code = 418


probe_api = NinjaAPI(urls_namespace="envelope-probe")
register_exception_handlers(probe_api)


@probe_api.get("/boom")
def boom(request: HttpRequest) -> None:
    raise TeapotRefusedError("رسالة مترجمة", extra={"fields": {"tea": ["نفدت"]}})


urlpatterns = [path("probe/", probe_api.urls)]


@pytest.mark.urls(__name__)
@pytest.mark.django_db  # middleware (sessions/axes) touches the DB
def test_application_error_envelope_carries_machine_code(client: Client) -> None:
    response = client.get("/probe/boom")

    assert response.status_code == 418
    body = response.json()
    assert set(body) == {"message", "extra"}
    assert body["extra"]["code"] == "teapot_refused"
    assert body["extra"]["fields"] == {"tea": ["نفدت"]}
