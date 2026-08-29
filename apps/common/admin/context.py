from dataclasses import dataclass
from typing import cast

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import AnonymousUser
from django.db.models import Model
from django.http import HttpRequest


@dataclass(frozen=True, slots=True)
class AdminContext:
    """What a field rule may consider when deciding visibility/editability."""

    request: HttpRequest
    obj: Model | None  # None on the add view

    @property
    def is_add(self) -> bool:
        return self.obj is None

    @property
    def is_change(self) -> bool:
        return self.obj is not None

    @property
    def user(self) -> AbstractBaseUser | AnonymousUser:
        return self.request.user

    @property
    def is_superuser(self) -> bool:
        # The admin only ever sees AUTH_USER_MODEL (a PermissionsMixin user)
        # or AnonymousUser - both carry the flag; django-stubs types the
        # request attribute on the flag-less AbstractBaseUser.
        return cast("AbstractUser | AnonymousUser", self.request.user).is_superuser
