from typing import Any

from allauth.usersessions.models import UserSession
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.notifications.constants import NotificationKind
from apps.notifications.services import notification_send
from apps.payments.services import wallet_create
from apps.payments.services import wallet_currency_for
from apps.users.constants import Language
from apps.users.exceptions import UserError
from apps.users.models import User
from apps.users.tasks import send_welcome_email

# Fields a user may change about themselves (PATCH /users/me).
USER_UPDATABLE_FIELDS = frozenset({"name", "phone", "language"})


def user_create(*, user: User, email: str, name: str, language: Language) -> User:
    """THE signup path - every adapter (account today, social when built)
    creates users through it, so the post-signup side effects run exactly
    once and ride the signup transaction.

    ``user`` is the blank instance the adapter's framework built (allauth
    hands its own to ``save_user`` and keeps using that object afterwards);
    the service owns every field on it. Regular users are passwordless
    (unusable password; email codes log them in). The wallet is provisioned
    HERE (explicit cross-app service call) - the payment flows only ever
    ``wallet_get`` and assume it exists. The WELCOME inbox row is inbox-only
    by catalog default (devices register after login, so a push would have
    nowhere to go yet).
    """
    with transaction.atomic():
        user.email = User.objects.normalize_email(email)
        user.name = name
        user.language = language
        user.set_unusable_password()
        user.full_clean()
        user.save()
        wallet_create(user=user, currency=wallet_currency_for(language=language))
        notification_send(
            recipient=user, kind=NotificationKind.WELCOME, context={"name": name}
        )
        transaction.on_commit(lambda: send_welcome_email.enqueue(str(user.pk)))
    return user


def user_update(*, user: User, data: dict[str, Any]) -> User:
    """Apply a partial update; callers pass only-sent-keys payloads
    (PatchDict in the API layer)."""
    for field, value in data.items():
        if field not in USER_UPDATABLE_FIELDS:
            raise UserError(str(_("Field not updatable: %(field)s") % {"field": field}))
        setattr(user, field, value)
    user.full_clean()
    user.save(update_fields=[*data.keys(), "updated_at"])
    return user


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
