"""User factories - factory_boy structure, mimesis values (apps.common.tests.fake)."""

import random
from typing import Any

from allauth.account.models import EmailAddress
from factory.declarations import LazyAttribute
from factory.declarations import LazyFunction
from factory.declarations import Sequence
from factory.declarations import Trait
from factory.django import DjangoModelFactory
from factory.helpers import post_generation

from apps.common.tests import fake
from apps.users.constants import Language
from apps.users.models import User

# Arabic-first site: most generated users default to Arabic.
_LANGUAGES = (Language.ARABIC, Language.ARABIC, Language.ARABIC, Language.ENGLISH)


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True

    email = Sequence(lambda n: f"user{n}@example.com")
    language = LazyFunction(lambda: random.choice(_LANGUAGES))  # noqa: S311
    name = LazyAttribute(lambda user: fake.full_name(user.language))
    password = "!"  # noqa: S105 - unusable marker: regular users are passwordless

    class Params:
        staff = Trait(is_staff=True)

    @post_generation
    def verified_email(self, create: bool, extracted: Any, **kwargs: Any) -> None:
        """Verified primary EmailAddress - without it, code-login pends on
        verify_email (ACCOUNT_EMAIL_VERIFICATION=mandatory)."""
        if not create:
            return
        EmailAddress.objects.get_or_create(
            user=self,
            email=self.email,
            defaults={"primary": True, "verified": True},
        )
