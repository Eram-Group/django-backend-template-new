from typing import Any
from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _
from phonenumber_field.modelfields import PhoneNumberField

from apps.common.models import BaseModel
from apps.users.constants import Language


class UserManager(DjangoUserManager["User"]):
    """Email-based manager for the accounts services do not create: staff
    with passwords (``createsu``) and test fixtures. Signups go through
    ``services.user_create``."""

    def _create_user(
        self,
        email: str,
        password: str | None,
        *,
        is_staff: bool,
        is_superuser: bool,
        **extra_fields: Any,
    ) -> User:
        if not email:
            msg = "The email address must be set."
            raise ValueError(msg)
        user = self.model(
            email=self.normalize_email(email),
            is_staff=is_staff,
            is_superuser=is_superuser,
            **extra_fields,
        )
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(  # type: ignore[override]
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ) -> User:
        return self._create_user(
            email, password, is_staff=False, is_superuser=False, **extra_fields
        )

    def create_superuser(  # type: ignore[override]
        self,
        email: str,
        password: str | None = None,
        **extra_fields: Any,
    ) -> User:
        return self._create_user(
            email, password, is_staff=True, is_superuser=True, **extra_fields
        )


class User(BaseModel, AbstractUser):
    """Application user: email login, single name field, passwordless by default.

    Staff/superusers keep (Argon2) passwords for admin login; regular users
    authenticate via email codes / social login only.
    """

    username = None  # type: ignore[assignment]
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]

    email = models.EmailField(_("email address"), unique=True)
    name = models.CharField(_("name"), max_length=255)
    # Optional and NOT unique - email is the login identity. Stored E164 with
    # no default region: clients submit the country code (+966... / +20...).
    # Optional; payments pass it to the gateway as customer_phone when present.
    phone = PhoneNumberField(_("phone number"), blank=True)
    # Set explicitly at creation (signup captures Accept-Language); drives
    # every user-facing email and notification.
    language = models.CharField(_("language"), max_length=2, choices=Language)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    def __str__(self) -> str:
        return self.email

    # AbstractUser builds these from first_name/last_name, which are removed
    # above - inherited versions would render the literal string "None None".
    def get_full_name(self) -> str:
        return self.name

    def get_short_name(self) -> str:
        return self.name
