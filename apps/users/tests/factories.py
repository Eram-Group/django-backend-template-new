"""User factories - factory_boy structure, mimesis values (apps.common.tests.fake)."""

import random
import uuid
from typing import Any

from allauth.account.models import EmailAddress
from factory.declarations import LazyAttribute
from factory.declarations import LazyFunction
from factory.declarations import RelatedFactory
from factory.declarations import Sequence
from factory.declarations import Trait
from factory.django import DjangoModelFactory
from factory.helpers import post_generation

from apps.common.tests import fake
from apps.users.constants import Language
from apps.users.models import User

# Arabic-first site: most generated users default to Arabic.
_LANGUAGES = (Language.ARABIC, Language.ARABIC, Language.ARABIC, Language.ENGLISH)

# Sequences restart at 0 in every process, but --reuse-db keeps rows that
# session fixtures committed in earlier runs - without a per-process tag,
# get_or_create would silently return those stale users.
_RUN_TAG = uuid.uuid4().hex[:6]


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True

    email = Sequence(lambda n: f"user{n}.{_RUN_TAG}@example.com")
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

    # Signup provisions a wallet (user_post_signup) - factory-made users
    # satisfy the same invariant. Dotted path: payments factories import
    # this module, so importing WalletFactory back would be circular;
    # WalletFactory's django_get_or_create=["user"] keeps this idempotent.
    wallet = RelatedFactory(
        "apps.payments.tests.factories.WalletFactory", factory_related_name="user"
    )
