from typing import Any

import pytest
from django.core import mail
from django.test import Client

from apps.users.adapters import AccountAdapter
from apps.users.models import User
from config.env import env

pytestmark = pytest.mark.django_db


def test_login_and_verification_codes_are_six_digits() -> None:
    adapter = AccountAdapter()
    for _ in range(20):
        assert adapter.generate_login_code().isdigit()
        assert len(adapter.generate_login_code()) == 6
        assert adapter.generate_email_verification_code().isdigit()
        assert len(adapter.generate_email_verification_code()) == 6


def test_signup_kill_switch(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "ACCOUNT_ALLOW_REGISTRATION", False)

    response = client.post(
        "/_allauth/app/v1/auth/signup",
        {"email": "blocked@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 403
    assert not User.objects.filter(email="blocked@example.com").exists()


def test_signup_captures_language_and_welcome_email_uses_it(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    """An English-locale signup must not get the Arabic-default welcome
    email - Accept-Language is captured at save_user time."""
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "locale.probe@example.com"},
            content_type="application/json",
            headers={"Accept-Language": "en"},
        )

    assert response.status_code == 401  # pending login_by_code
    user = User.objects.get(email="locale.probe@example.com")
    assert user.language == "en"
    welcome = [m for m in mail.outbox if "Welcome" in m.subject]
    assert welcome, [m.subject for m in mail.outbox]


def test_signup_defaults_to_arabic_without_accept_language(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "locale.default@example.com"},
            content_type="application/json",
        )

    user = User.objects.get(email="locale.default@example.com")
    assert user.language == "ar"


def test_signup_triggers_welcome_email_after_commit(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    """The no-signals chain: adapter.save_user -> service -> task on_commit."""
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "chain.probe@example.com"},
            content_type="application/json",
        )

    assert response.status_code == 401  # pending login_by_code
    recipients = [message.to for message in mail.outbox]
    subjects = [message.subject for message in mail.outbox]
    assert [["chain.probe@example.com"]] * len(mail.outbox) == recipients
    # Both the verification-code email and the welcome task's email went out.
    assert any("Welcome" in subject for subject in subjects), subjects
    assert len(mail.outbox) >= 2
