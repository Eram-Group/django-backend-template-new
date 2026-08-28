from typing import Any

import pytest
from allauth.account.models import EmailAddress
from allauth.usersessions.models import UserSession
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage

from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.payments.constants import DEFAULT_CURRENCY
from apps.users import services
from apps.users.constants import Language
from apps.users.models import User
from apps.users.services.users import USER_UPDATABLE_FIELDS
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_update_applies_allowed_fields() -> None:
    user = UserFactory.create(language=Language.ARABIC)
    before = user.updated_at

    updated = services.user_update(
        user=user, data={"name": "Omar", "language": Language.ENGLISH}
    )

    updated.refresh_from_db()
    assert updated.name == "Omar"
    assert updated.language == Language.ENGLISH
    assert updated.updated_at > before


def test_user_update_rejects_non_updatable_field() -> None:
    user = UserFactory.create()
    with pytest.raises(ValueError, match="Field not updatable: email"):
        services.user_update(user=user, data={"email": "evil@example.com"})


def test_user_update_runs_model_validation() -> None:
    user = UserFactory.create()
    with pytest.raises(ValidationError) as excinfo:
        services.user_update(user=user, data={"language": "xx"})
    assert "language" in excinfo.value.message_dict


def test_user_post_signup_provisions_wallet_and_enqueues_welcome_email(
    django_capture_on_commit_callbacks: Any,
    mailoutbox: list[EmailMessage],
) -> None:
    # Bare create_user, like real signup: no factory-provisioned wallet yet.
    user = User.objects.create_user("signup@example.com", name="New User")

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        services.user_post_signup(user=user)

    assert user.wallet.currency == DEFAULT_CURRENCY
    assert user.wallet.balance == 0
    # ImmediateBackend (test settings) ran the enqueued task synchronously.
    assert len(callbacks) == 1
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    # WELCOME is inbox-only by catalog default (no devices exist at signup).
    welcome = Notification.objects.get(recipient=user, kind=NotificationKind.WELCOME)
    assert welcome.context == {"name": "New User"}
    assert not welcome.deliveries.exists()


def test_user_post_signup_sends_nothing_before_commit(
    mailoutbox: list[EmailMessage],
) -> None:
    user = User.objects.create_user("signup@example.com", name="New User")
    services.user_post_signup(user=user)  # transaction never commits in tests
    assert not mailoutbox


def test_updatable_fields_are_a_safe_allowlist() -> None:
    assert {"name", "phone", "language"} == USER_UPDATABLE_FIELDS
    forbidden = {"email", "is_staff", "is_superuser", "password"}
    assert not (USER_UPDATABLE_FIELDS & forbidden)


def test_factory_users_are_passwordless_and_verified() -> None:
    user = UserFactory.create()
    assert not user.has_usable_password()
    assert EmailAddress.objects.filter(user=user, verified=True, primary=True).exists()
    assert User.objects.filter(pk=user.pk).exists()


def test_user_deactivate_flips_is_active_and_ends_sessions() -> None:
    user = UserFactory.create()
    UserSession.objects.create(
        user=user,
        ip="127.0.0.1",
        session_key="deactivate-probe-session-key",
        user_agent="probe",
    )

    services.user_deactivate(user=user)

    user.refresh_from_db()
    assert not user.is_active
    assert not UserSession.objects.filter(user=user).exists()
