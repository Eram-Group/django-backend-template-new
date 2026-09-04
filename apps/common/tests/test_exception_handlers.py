"""Envelope contract: ApplicationError responses carry extra['code'].

A probe NinjaAPI (this module doubles as its urlconf via pytest.mark.urls)
routes a real request through config/api/exception_handlers.py - messages
are localized, so the stable code is what clients branch on.
"""

import pytest
from django.http import HttpRequest
from django.http import HttpResponse
from django.test import Client
from django.urls import path
from ninja import NinjaAPI
from ninja.errors import Throttled

from apps.common.exceptions import ApplicationError
from apps.common.pagination import _decode_cursor
from config.api.exception_handlers import register_exception_handlers


class TeapotRefusedError(ApplicationError):
    status_code = 418


probe_api = NinjaAPI(urls_namespace="envelope-probe")
register_exception_handlers(probe_api)


@probe_api.get("/boom")
def boom(request: HttpRequest) -> None:
    raise TeapotRefusedError("رسالة مترجمة", extra={"fields": {"tea": ["نفدت"]}})


@probe_api.get("/throttled")
def throttled(request: HttpRequest) -> None:
    raise Throttled(wait=7)


@probe_api.get("/cursor")
def bad_cursor(request: HttpRequest) -> None:
    _decode_cursor("not-a-cursor")


def crash(request: HttpRequest) -> HttpResponse:
    msg = "unhandled probe crash"
    raise RuntimeError(msg)


urlpatterns = [
    path("probe/", probe_api.urls),
    path("api/crash", crash),
    path("crash", crash),
]

# The real project handler (config.urls) - wired here so pytest.mark.urls
# resolves it exactly the way production's ROOT_URLCONF does.
handler500 = "config.urls.handle_500"


@pytest.mark.urls(__name__)
@pytest.mark.django_db  # middleware (sessions/axes) touches the DB
def test_application_error_envelope_carries_machine_code(client: Client) -> None:
    response = client.get("/probe/boom")

    assert response.status_code == 418
    body = response.json()
    assert set(body) == {"message", "extra"}
    assert body["extra"]["code"] == "teapot_refused"
    assert body["extra"]["fields"] == {"tea": ["نفدت"]}


@pytest.mark.urls(__name__)
@pytest.mark.django_db
def test_unhandled_crash_keeps_json_envelope_on_api_paths() -> None:
    client = Client(raise_request_exception=False)

    response = client.get("/api/crash")

    assert response.status_code == 500
    assert set(response.json()) == {"message", "extra"}


@pytest.mark.urls(__name__)
@pytest.mark.django_db
def test_unhandled_crash_stays_html_off_api_paths() -> None:
    client = Client(raise_request_exception=False)

    response = client.get("/crash")

    assert response.status_code == 500
    assert response.headers["Content-Type"].startswith("text/html")


@pytest.mark.urls(__name__)
@pytest.mark.django_db
def test_throttled_response_carries_retry_after(client: Client) -> None:
    response = client.get("/probe/throttled")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "7"
    assert set(response.json()) == {"message", "extra"}


@pytest.mark.urls(__name__)
@pytest.mark.django_db
def test_bad_cursor_has_its_own_code(client: Client) -> None:
    response = client.get("/probe/cursor")

    assert response.status_code == 400
    assert response.json()["extra"]["code"] == "invalid_cursor"
