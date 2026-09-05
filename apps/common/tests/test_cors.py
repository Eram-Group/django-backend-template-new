"""CORS is scoped to the API surfaces - the admin must never carry
credentialed cross-origin headers for the frontend origins."""

import pytest
from django.conf import settings
from django.test import Client

pytestmark = pytest.mark.django_db


def _origin() -> str:
    return str(settings.CORS_ALLOWED_ORIGINS[0])


def test_api_responses_carry_cors_headers(client: Client) -> None:
    response = client.get("/api/v1/docs", HTTP_ORIGIN=_origin())
    assert response.headers.get("Access-Control-Allow-Origin") == _origin()


def test_allauth_responses_carry_cors_headers(client: Client) -> None:
    response = client.get("/_allauth/browser/v1/config", HTTP_ORIGIN=_origin())
    assert response.headers.get("Access-Control-Allow-Origin") == _origin()


def test_preflight_allows_the_session_token_header(client: Client) -> None:
    """The app-client auth flow (X-Session-Token, config/api/auth.py) must
    survive a browser preflight from an allowed origin."""
    response = client.options(
        "/api/v1/users/me",
        HTTP_ORIGIN=_origin(),
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="PATCH",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type,x-session-token",
    )
    allowed = response.headers.get("Access-Control-Allow-Headers", "").lower()
    assert "x-session-token" in allowed
    assert "content-type" in allowed


def test_admin_responses_never_carry_cors_headers(client: Client) -> None:
    response = client.get("/admin/login/", HTTP_ORIGIN=_origin())
    assert "Access-Control-Allow-Origin" not in response.headers


def test_ssl_redirect_still_carries_cors_headers(
    client: Client, settings: object
) -> None:
    """CorsMiddleware sits above SecurityMiddleware: the 301 a plain-http API
    call gets in production must carry the CORS header, or the browser
    fails the request instead of following the redirect."""
    from django.test import override_settings

    with override_settings(SECURE_SSL_REDIRECT=True):
        response = client.get("/api/v1/docs", HTTP_ORIGIN=_origin())
    assert response.status_code == 301
    assert response.headers.get("Access-Control-Allow-Origin") == _origin()
