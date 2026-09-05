from typing import Any

import pytest
from allauth.account.models import EmailAddress
from allauth.usersessions.models import UserSession
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage

from apps.notifications.constants import NotificationKind
from apps.notifications.models import Notification
from apps.payments.services import wallet_currency_for
from apps.users import services
from apps.users.constants import Language
from apps.users.exceptions import UserError
from apps.users.models import User
from apps.users.services.users import USER_UPDATABLE_FIELDS
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_fixture_is_saved_verified_and_passwordless(user: User) -> None:
    assert not user._state.adding  # pk is a DatabaseDefault sentinel until saved
    assert not user.has_usable_password()
    assert EmailAddress.objects.filter(user=user, verified=True, primary=True).exists()


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
    with pytest.raises(UserError, match="Field not updatable: email"):
        services.user_update(user=user, data={"email": "evil@example.com"})


def test_user_update_runs_model_validation() -> None:
    user = UserFactory.create()
    with pytest.raises(ValidationError) as excinfo:
        services.user_update(user=user, data={"language": "xx"})
    assert "language" in excinfo.value.message_dict


def test_user_update_rejects_an_empty_name() -> None:
    user = UserFactory.create()
    with pytest.raises(ValidationError) as excinfo:
        services.user_update(user=user, data={"name": ""})
    assert "name" in excinfo.value.message_dict


def test_user_create_provisions_wallet_and_enqueues_welcome_email(
    run_enqueued_tasks: Any,
    mailoutbox: list[EmailMessage],
) -> None:
    with run_enqueued_tasks() as records:
        user = services.user_create(
            user=User(),
            email="Signup@Example.com",
            name="New User",
            language=Language.ENGLISH,
        )

    assert user.email == "Signup@example.com"  # domain normalized
    assert user.language == Language.ENGLISH
    assert not user.has_usable_password()
    assert user.wallet.currency == wallet_currency_for(language=Language.ENGLISH)
    assert user.wallet.balance == 0
    # The drained queue ran the welcome-email task.
    assert len(records) == 1
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [user.email]
    # WELCOME is inbox-only by catalog default (no devices exist at signup).
    welcome = Notification.objects.get(recipient=user, kind=NotificationKind.WELCOME)
    assert welcome.context == {"name": "New User"}
    assert not welcome.deliveries.exists()


def test_user_create_sends_nothing_before_commit(
    mailoutbox: list[EmailMessage],
) -> None:
    services.user_create(
        user=User(),
        email="signup@example.com",
        name="New User",
        language=Language.ARABIC,
    )  # transaction never commits in tests
    assert not mailoutbox


def test_user_create_requires_a_name_and_a_unique_email() -> None:
    UserFactory.create(email="taken@example.com")
    with pytest.raises(ValidationError) as excinfo:
        services.user_create(
            user=User(), email="taken@example.com", name="", language=Language.ARABIC
        )
    assert {"email", "name"} <= set(excinfo.value.message_dict)
    assert User.objects.filter(email="taken@example.com").count() == 1


def test_updatable_fields_are_a_safe_allowlist() -> None:
    assert {"name", "phone", "language"} == USER_UPDATABLE_FIELDS
    forbidden = {"email", "is_staff", "is_superuser", "password"}
    assert not (USER_UPDATABLE_FIELDS & forbidden)


def test_factory_users_are_passwordless_and_verified() -> None:
    user = UserFactory.create()
    assert not user.has_usable_password()
    assert EmailAddress.objects.filter(user=user, verified=True, primary=True).exists()
    assert User.objects.filter(pk=user.pk).exists()


def test_manager_sets_the_privilege_flags_explicitly() -> None:
    regular = User.objects.create_user(
        "regular@example.com", name="Regular", language=Language.ARABIC
    )
    boss = User.objects.create_superuser(
        "boss@example.com", "pw", name="Boss", language=Language.ENGLISH
    )
    assert (regular.is_staff, regular.is_superuser) == (False, False)
    assert (boss.is_staff, boss.is_superuser) == (True, True)
    assert boss.check_password("pw")
    assert not regular.has_usable_password()


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
