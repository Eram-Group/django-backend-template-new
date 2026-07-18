import uuid

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.users.exceptions import UserNotFoundError
from apps.users.models import User


def user_get(*, pk: uuid.UUID) -> User:
    """Fetch a single user; fields match what UserDetail serializes."""
    try:
        return User.objects.get(pk=pk)
    except User.DoesNotExist as exc:
        raise UserNotFoundError(str(_("User not found."))) from exc


def user_list(*, is_active: bool | None = None) -> QuerySet[User]:
    """Base queryset for paginated user lists (CursorPagination orders by pk)."""
    users = User.objects.all()
    if is_active is not None:
        users = users.filter(is_active=is_active)
    return users
