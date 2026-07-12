"""API tests for /api/v1/users/me - envelope contract + both auth paths."""

import re
from typing import Any

import pytest
from django.core import mail
from django.test import Client

from apps.users.constants import Language
from apps.users.models import User
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

ME = "/api/v1/users/me"


def test_me_unauthenticated_returns_401_envelope(client: Client) -> None:
    response = client.get(ME)
    assert response.status_code == 401
    body = response.json()
    assert set(body) == {"message", "extra"}


def test_me_returns_current_user(client: Client) -> None:
    user = UserFactory.create(name="Reader", language=Language.ARABIC)
    client.force_login(user)

    body = client.get(ME).json()

    assert body["id"] == str(user.pk)
    assert body["email"] == user.email
    assert body["name"] == "Reader"
    assert body["language"] == "ar"
    assert "created_at" in body


def test_me_patch_partial_update_keeps_unsent_fields(client: Client) -> None:
    user = UserFactory.create(name="Before", language=Language.ARABIC)
    client.force_login(user)

    response = client.patch(ME, {"name": "After"}, content_type="application/json")

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.name == "After"
    assert user.language == Language.ARABIC  # exclude_unset: not wiped to None


def test_me_patch_empty_body_changes_nothing(client: Client) -> None:
    user = UserFactory.create(name="Same")
    client.force_login(user)

    assert client.patch(ME, {}, content_type="application/json").status_code == 200
    user.refresh_from_db()
    assert user.name == "Same"


def test_me_patch_invalid_language_returns_422_envelope(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)

    response = client.patch(ME, {"language": "xx"}, content_type="application/json")

    assert response.status_code == 422
    body = response.json()
    assert body["message"] == "Validation error."
    fields = body["extra"]["fields"]
    assert any("language" in key for key in fields)
    assert all(isinstance(messages, list) for messages in fields.values())


def test_me_patch_cannot_touch_protected_fields(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)
    original_email = user.email

    response = client.patch(
        ME,
        {"email": "evil@example.com", "is_staff": True, "name": "Legit"},
        content_type="application/json",
    )

    assert response.status_code == 200  # unknown fields ignored by the schema
    user.refresh_from_db()
    assert user.email == original_email
    assert not user.is_staff
    assert user.name == "Legit"


def test_me_delete_deactivates_and_kills_sessions(client: Client) -> None:
    """Store-mandated account removal: 204, is_active off, and the very
    credentials used for the call stop working immediately."""
    user = UserFactory.create()
    client.force_login(user)

    response = client.delete(ME)

    assert response.status_code == 204
    user.refresh_from_db()
    assert not user.is_active
    # The session cookie the client still holds must be dead.
    assert client.get(ME).status_code == 401


def test_full_passwordless_flow_and_x_session_token(client: Client) -> None:
    """signup -> email code -> confirm -> call the API with X-Session-Token."""
    email = "flow.probe@example.com"
    headers: dict[str, Any] = {"content_type": "application/json"}

    # Passwordless signup: allauth answers 401 with login_by_code pending
    # and emails a 6-digit code (which doubles as email verification).
    signup = client.post("/_allauth/app/v1/auth/signup", {"email": email}, **headers)
    assert signup.status_code == 401
    body = signup.json()
    pending = [f["id"] for f in body["data"]["flows"] if f.get("is_pending")]
    assert pending == ["login_by_code"]
    session_token = body["meta"]["session_token"]
    assert User.objects.filter(email=email).exists()

    code_match = re.search(r"\b(\d{6})\b", str(mail.outbox[-1].body))
    assert code_match, mail.outbox[-1].body
    confirmed = client.post(
        "/_allauth/app/v1/auth/code/confirm",
        {"code": code_match.group(1)},
        headers={"X-Session-Token": session_token},
        **headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["meta"]["is_authenticated"] is True
    session_token = confirmed.json()["meta"]["session_token"]

    me = client.get(ME, headers={"X-Session-Token": session_token})
    assert me.status_code == 200
    assert me.json()["email"] == email
