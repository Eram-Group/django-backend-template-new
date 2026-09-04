"""API tests for /api/v1/users/me - envelope contract + both auth paths."""

import re
from typing import Any
from typing import cast

import pytest
from django.conf import settings
from django.core.mail import EmailMessage
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


def test_me_patch_invalid_language_returns_422_envelope(auth_client: Client) -> None:
    response = auth_client.patch(
        ME, {"language": "xx"}, content_type="application/json"
    )

    assert response.status_code == 422
    body = response.json()
    assert body["message"] == "Validation error."
    fields = body["extra"]["fields"]
    assert any("language" in key for key in fields)
    assert all(isinstance(messages, list) for messages in fields.values())


def test_me_patch_sets_phone_and_detail_echoes_it(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)

    response = client.patch(
        ME, {"phone": "+966501234567"}, content_type="application/json"
    )

    assert response.status_code == 200
    assert response.json()["phone"] == "+966501234567"
    user.refresh_from_db()
    assert str(user.phone) == "+966501234567"


def test_me_patch_invalid_phone_returns_400_envelope(auth_client: Client) -> None:
    # No country code -> not parseable (no default region is configured).
    response = auth_client.patch(
        ME, {"phone": "12345"}, content_type="application/json"
    )

    assert response.status_code == 400
    assert "phone" in response.json()["extra"]["fields"]


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


def test_full_passwordless_flow_and_x_session_token(
    client: Client, mailoutbox: list[EmailMessage]
) -> None:
    """signup -> email code -> confirm -> call the API with X-Session-Token."""
    email = "flow.probe@example.com"
    headers: dict[str, Any] = {"content_type": "application/json"}

    # Passwordless signup: allauth answers 401 with login_by_code pending
    # and emails a 6-digit code (which doubles as email verification).
    signup = client.post(
        "/_allauth/app/v1/auth/signup", {"email": email, "name": "Flow"}, **headers
    )
    assert signup.status_code == 401
    body = signup.json()
    pending = [f["id"] for f in body["data"]["flows"] if f.get("is_pending")]
    assert pending == ["login_by_code"]
    session_token = body["meta"]["session_token"]
    assert User.objects.filter(email=email).exists()

    code_match = re.search(r"\b(\d{6})\b", str(mailoutbox[-1].body))
    assert code_match, mailoutbox[-1].body
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


def _app_client_session_token(
    client: Client, mailoutbox: list[EmailMessage], email: str
) -> str:
    """Passwordless signup as an allauth *app* client: returns the session
    token of an authenticated session and sets NO cookie on the client."""
    headers: dict[str, Any] = {"content_type": "application/json"}
    signup = client.post(
        "/_allauth/app/v1/auth/signup", {"email": email, "name": "App"}, **headers
    )
    assert signup.status_code == 401
    pending_token = signup.json()["meta"]["session_token"]
    code_match = re.search(r"\b(\d{6})\b", str(mailoutbox[-1].body))
    assert code_match
    confirmed = client.post(
        "/_allauth/app/v1/auth/code/confirm",
        {"code": code_match.group(1)},
        headers={"X-Session-Token": pending_token},
        **headers,
    )
    assert confirmed.status_code == 200
    token: str = confirmed.json()["meta"]["session_token"]
    return token


def test_session_token_client_writes_without_csrf(
    mailoutbox: list[EmailMessage],
) -> None:
    """App clients carry no cookie and no CSRF token: unsafe methods must
    succeed on X-Session-Token alone. Regression: with the cookie auth first
    in ``api_auth`` ninja ran its CSRF check before the header auth and every
    PATCH/POST/DELETE from a mobile client was a 403 - invisible to tests
    using ``force_login`` because the test client skips CSRF by default."""
    client = Client(enforce_csrf_checks=True)
    token = _app_client_session_token(client, mailoutbox, "app.client@example.com")

    response = client.patch(
        ME,
        {"name": "Renamed"},
        content_type="application/json",
        headers={"X-Session-Token": token},
    )

    assert response.status_code == 200, response.content
    assert response.json()["name"] == "Renamed"
    assert client.cookies.get(settings.SESSION_COOKIE_NAME) is None


def test_cookie_client_unsafe_method_still_requires_csrf() -> None:
    """The browser path keeps CSRF: a session cookie without the token is 403,
    and the same request with the token is 200."""
    user = UserFactory.create(name="Browser")
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)

    denied = client.patch(ME, {"name": "Tampered"}, content_type="application/json")
    assert denied.status_code == 403
    assert denied.json() == {"message": "CSRF check Failed", "extra": {}}
    user.refresh_from_db()
    assert user.name == "Browser"

    # The SPA reads the (non-HttpOnly) CSRF cookie that allauth's browser
    # config endpoint sets and echoes it in X-CSRFToken.
    assert client.get("/_allauth/browser/v1/config").status_code == 200
    csrf_token = client.cookies[settings.CSRF_COOKIE_NAME].value
    allowed = client.patch(
        ME,
        {"name": "Legit"},
        content_type="application/json",
        headers={"X-CSRFToken": csrf_token},
    )
    assert allowed.status_code == 200, allowed.content


def test_unknown_email_code_request_sends_the_unknown_account_mail(
    client: Client, mailoutbox: list[EmailMessage]
) -> None:
    """No enumeration: an unknown address gets the same 401 as a known one,
    and allauth mails it a signup pointer - through OUR template. Regression:
    the template was missing, so every unknown-email request was a 500."""
    response = client.post(
        "/_allauth/app/v1/auth/code/request",
        {"email": "nobody@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert [m.to for m in mailoutbox] == [["nobody@example.com"]]
    body = mailoutbox[-1].body
    assert settings.SITE_NAME in body  # rendered through emails/base.html
    assert settings.FRONTEND_BASE_URL in body  # the signup link


def test_signup_with_existing_email_sends_the_already_exists_mail(
    client: Client, mailoutbox: list[EmailMessage]
) -> None:
    """Same no-enumeration rule on signup: the owner is told by email, the
    caller sees the ordinary pending-verification answer."""
    user = UserFactory.create()

    response = client.post(
        "/_allauth/app/v1/auth/signup",
        {"email": user.email, "name": "Impostor"},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert [m.to for m in mailoutbox] == [[user.email]]
    assert settings.SITE_NAME in mailoutbox[-1].body  # rendered, not a 500
    user.refresh_from_db()
    assert user.name != "Impostor"


def test_login_code_requests_are_throttled_per_ip(
    client: Client, mailoutbox: list[EmailMessage]
) -> None:
    """allauth's ``request_login_code`` limit (20/m per ip) is what stops one
    address from mail-bombing many inboxes - a contract the template relies
    on, so a changed upstream default fails here, not in production. allauth
    reports it as a 400 form error (``too_many_login_attempts``), not a 429
    (docs/AUTH_API.md)."""
    responses = [
        client.post(
            "/_allauth/app/v1/auth/code/request",
            {"email": f"probe{i}@example.com"},
            content_type="application/json",
        )
        for i in range(21)
    ]

    # Unknown emails still "pend" (no enumeration) - and each got a mail.
    assert [r.status_code for r in responses[:20]] == [401] * 20
    assert len(mailoutbox) == 20
    assert responses[20].status_code == 400
    assert "too_many_login_attempts" in responses[20].content.decode()
    assert len(mailoutbox) == 20


def test_api_wide_throttle_is_per_principal(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The API-wide ceiling (600/m) closes on the request past it for one
    user and answers with the envelope; ninja checks it after auth, so it
    never shadows the 401. The window is shrunk to 5 for the test - 601
    real requests proved the same contract at 40x the cost."""
    from ninja.throttling import SimpleRateThrottle

    from config.api.v1 import api

    (throttle,) = cast("list[Any]", api.throttle)
    assert isinstance(throttle, SimpleRateThrottle)
    assert throttle.rate == "600/m"
    monkeypatch.setattr(throttle, "num_requests", 5)
    client.force_login(UserFactory.create())
    statuses = [client.get(ME).status_code for _ in range(6)]

    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429
    assert client.get(ME).json() == {"message": "Too many requests.", "extra": {}}
    assert Client().get(ME).status_code == 401
