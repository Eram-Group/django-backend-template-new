import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.users.exceptions import UserNotFoundError
from apps.users.models import User


def get_user_by_id(*, pk: uuid.UUID) -> User:
    try:
        return User.objects.get(pk=pk)
    except User.DoesNotExist as exc:
        raise UserNotFoundError(str(_("User not found."))) from exc


def get_user_list(*, is_active: bool | None = None) -> QuerySet[User]:
    users = User.objects.all()
    if is_active is not None:
        users = users.filter(is_active=is_active)
    return users


def get_user_count(*, is_active: bool | None = None) -> int:
    return get_user_list(is_active=is_active).count()
