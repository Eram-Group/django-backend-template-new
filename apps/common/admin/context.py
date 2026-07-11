from dataclasses import dataclass

from django.contrib.auth.base_user import AbstractBaseUser
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
