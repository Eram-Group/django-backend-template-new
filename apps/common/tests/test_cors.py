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


def test_admin_responses_never_carry_cors_headers(client: Client) -> None:
    response = client.get("/admin/login/", HTTP_ORIGIN=_origin())
    assert "Access-Control-Allow-Origin" not in response.headers
