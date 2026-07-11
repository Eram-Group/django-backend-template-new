from typing import Any

import pytest
from allauth.account.models import EmailAddress
from django.core import mail
from django.core.exceptions import ValidationError

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


def test_user_post_signup_enqueues_welcome_email_on_commit(
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create()

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        services.user_post_signup(user=user)

    # ImmediateBackend (test settings) ran the enqueued task synchronously.
    assert len(callbacks) == 1
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]


def test_user_post_signup_sends_nothing_before_commit() -> None:
    user = UserFactory.create()
    services.user_post_signup(user=user)  # transaction never commits in tests
    assert not mail.outbox


def test_updatable_fields_are_a_safe_allowlist() -> None:
    assert {"name", "language"} == USER_UPDATABLE_FIELDS
    forbidden = {"email", "is_staff", "is_superuser", "password"}
    assert not (USER_UPDATABLE_FIELDS & forbidden)


def test_factory_users_are_passwordless_and_verified() -> None:
    user = UserFactory.create()
    assert not user.has_usable_password()
    assert EmailAddress.objects.filter(user=user, verified=True, primary=True).exists()
    assert User.objects.filter(pk=user.pk).exists()
