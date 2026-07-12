from typing import Any

from allauth.usersessions.models import UserSession
from django.db import transaction

from apps.users.models import User
from apps.users.tasks import send_welcome_email

# Fields a user may change about themselves (PATCH /users/me).
USER_UPDATABLE_FIELDS = frozenset({"name", "language"})


def user_update(*, user: User, data: dict[str, Any]) -> User:
    """Apply a partial update; callers pass .dict(exclude_unset=True) payloads."""
    for field, value in data.items():
        if field not in USER_UPDATABLE_FIELDS:
            msg = f"Field not updatable: {field}"
            raise ValueError(msg)
        setattr(user, field, value)
    user.full_clean()
    user.save(update_fields=[*data.keys(), "updated_at"])
    return user


def user_post_signup(*, user: User) -> None:
    """Post-signup side effects; the task enqueue rides the signup transaction."""
    transaction.on_commit(lambda: send_welcome_email.enqueue(str(user.pk)))


def user_deactivate(*, user: User) -> None:
    """Store-mandated in-app account removal (Apple 5.1.1(v) / Google Play).

    is_active=False blocks future logins; ending every allauth session kills
    live browser cookies AND X-Session-Token credentials immediately (end()
    deletes the session-store row, not just the bookkeeping record).
    """
    user.is_active = False
    user.full_clean()
    user.save(update_fields=["is_active", "updated_at"])
    for session in UserSession.objects.filter(user=user):
        session.end()
