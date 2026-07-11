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
