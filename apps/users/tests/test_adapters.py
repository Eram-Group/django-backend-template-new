from typing import Any

import pytest
from django.conf import settings
from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives
from django.test import Client
from django.utils.translation import gettext

from apps.users.adapters import AccountAdapter
from apps.users.models import User

pytestmark = pytest.mark.django_db


def test_login_and_verification_codes_are_six_digits() -> None:
    adapter = AccountAdapter()
    for _ in range(20):
        assert adapter.generate_login_code().isdigit()
        assert len(adapter.generate_login_code()) == 6
        assert adapter.generate_email_verification_code().isdigit()
        assert len(adapter.generate_email_verification_code()) == 6


def test_login_code_email_is_branded_html_with_real_expiry(
    client: Client, mailoutbox: list[EmailMessage]
) -> None:
    """The OTP email (the product's first touchpoint) must be branded HTML;
    the expiry line renders from ACCOUNT_LOGIN_BY_CODE_TIMEOUT, never
    hardcoded copy (drift bug observed in the reference template)."""
    import re

    client.post(
        "/_allauth/app/v1/auth/signup",
        {"email": "html.code@example.com", "name": "Probe"},
        content_type="application/json",
    )

    message = mailoutbox[-1]
    assert message.subject.startswith(f"[{settings.SITE_NAME}] ")
    code_match = re.search(r"\b(\d{6})\b", str(message.body))
    assert code_match, message.body
    assert isinstance(message, EmailMultiAlternatives)
    assert message.alternatives, "code email must carry an HTML alternative"
    html_body = str(message.alternatives[0][0])
    assert code_match.group(1) in html_body
    # 180s default -> 3 minutes, rendered in the user's language.
    assert (
        gettext("This code expires in %(minutes)s minutes.") % {"minutes": 3}
        in html_body
    )


def test_signup_captures_language_and_welcome_email_uses_it(
    client: Client,
    django_capture_on_commit_callbacks: Any,
    mailoutbox: list[EmailMessage],
) -> None:
    """An English-locale signup must not get the Arabic-default welcome
    email - Accept-Language is captured at save_user time."""
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "locale.probe@example.com", "name": "Probe"},
            content_type="application/json",
            headers={"Accept-Language": "en"},
        )

    assert response.status_code == 401  # pending login_by_code
    user = User.objects.get(email="locale.probe@example.com")
    assert user.language == "en"
    welcome = [m for m in mailoutbox if "Welcome" in m.subject]
    assert welcome, [m.subject for m in mailoutbox]


def test_signup_defaults_to_arabic_without_accept_language(
    client: Client, django_capture_on_commit_callbacks: Any
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "locale.default@example.com", "name": "Probe"},
            content_type="application/json",
        )

    user = User.objects.get(email="locale.default@example.com")
    assert user.language == "ar"


def test_signup_without_a_name_is_rejected(client: Client) -> None:
    response = client.post(
        "/_allauth/app/v1/auth/signup",
        {"email": "nameless@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert [error["param"] for error in response.json()["errors"]] == ["name"]
    assert not User.objects.filter(email="nameless@example.com").exists()


def test_signup_triggers_welcome_email_after_commit(
    client: Client,
    django_capture_on_commit_callbacks: Any,
    mailoutbox: list[EmailMessage],
) -> None:
    """The no-signals chain: adapter.save_user -> service -> task on_commit."""
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/_allauth/app/v1/auth/signup",
            {"email": "chain.probe@example.com", "name": "Probe"},
            content_type="application/json",
        )

    assert response.status_code == 401  # pending login_by_code
    recipients = [message.to for message in mailoutbox]
    subjects = [message.subject for message in mailoutbox]
    assert [["chain.probe@example.com"]] * len(mailoutbox) == recipients
    # Both the verification-code email and the welcome task's email went out.
    assert gettext("Welcome!") in subjects, subjects
    assert len(mailoutbox) >= 2
