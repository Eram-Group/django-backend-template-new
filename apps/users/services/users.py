from typing import Any

from allauth.usersessions.models import UserSession
from django.db import transaction

from apps.payments.services import wallet_create
from apps.users.models import User
from apps.users.tasks import send_welcome_email

# Fields a user may change about themselves (PATCH /users/me).
USER_UPDATABLE_FIELDS = frozenset({"name", "phone", "language"})


def user_update(*, user: User, data: dict[str, Any]) -> User:
    for field, value in data.items():
        if field not in USER_UPDATABLE_FIELDS:
            raise ValueError(f"Field not updatable: {field}")
        setattr(user, field, value)
    user.full_clean()
    user.save(update_fields=[*data.keys(), "updated_at"])
    return user


def user_post_signup(*, user: User) -> None:
    wallet_create(user=user)
    transaction.on_commit(lambda: send_welcome_email.enqueue(str(user.pk)))


def user_deactivate(*, user: User) -> None:
    user.is_active = False
    user.full_clean()
    user.save(update_fields=["is_active", "updated_at"])

    user_sessions = UserSession.objects.filter(user=user)
    for session in user_sessions:
        session.end()
