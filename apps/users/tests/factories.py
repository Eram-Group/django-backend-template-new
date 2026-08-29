"""User factories - factory_boy structure, mimesis values (apps.common.tests.fake)."""

import random
import uuid

from allauth.account.models import EmailAddress
from factory.declarations import LazyAttribute
from factory.declarations import RelatedFactory
from factory.declarations import Sequence
from factory.declarations import SubFactory
from factory.declarations import Trait
from factory.django import DjangoModelFactory
from factory.fuzzy import FuzzyChoice

from apps.common.tests import fake
from apps.users.constants import Language
from apps.users.models import User

# Arabic-first site: most generated users are Arabic.
LANGUAGE_WEIGHTS = (
    Language.ARABIC,
    Language.ARABIC,
    Language.ARABIC,
    Language.ENGLISH,
)

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
    # FuzzyChoice draws from factory.random's reseedable generator -
    # seed_db's --seed calls factory.random.reseed_random for determinism.
    language = FuzzyChoice(LANGUAGE_WEIGHTS)
    name = LazyAttribute(lambda user: fake.full_name(user.language))
    password = "!"

    @classmethod
    def build_bulk(cls, *, emails: list[str], rng: random.Random) -> list[User]:
        """Unsaved users for seed_db's bulk path - the same user as the
        declarations above (weighted language, locale-matched name, unusable
        password), built with plain constructors: declaration resolution
        measured ~9x slower per instance and the user loop dominates seeding
        CPU. The verified EmailAddress and the wallet (RelatedFactory here)
        are bulk-created by the seeder."""
        users = []
        for email in emails:
            language = rng.choice(LANGUAGE_WEIGHTS)
            users.append(
                User(
                    email=email,
                    language=language,
                    name=fake.full_name(language),
                    password="!",
                )
            )
        return users

    class Params:
        staff = Trait(is_staff=True)

    # Verified primary EmailAddress - without it, code-login pends on
    # verify_email (ACCOUNT_EMAIL_VERIFICATION=mandatory). Dotted path:
    # EmailAddressFactory is defined below (forward reference).
    verified_email = RelatedFactory(
        "apps.users.tests.factories.EmailAddressFactory",
        factory_related_name="user",
    )

    # Signup provisions a wallet (user_create) - factory-made users
    # satisfy the same invariant. Dotted path: payments factories import
    # this module, so importing WalletFactory back would be circular;
    # WalletFactory's django_get_or_create=["user"] keeps this idempotent.
    wallet = RelatedFactory(
        "apps.payments.tests.factories.WalletFactory", factory_related_name="user"
    )


class EmailAddressFactory(DjangoModelFactory[EmailAddress]):
    """allauth EmailAddress (third-party, so outside the coverage gate) -
    exists to serve UserFactory.verified_email."""

    class Meta:
        model = EmailAddress
        django_get_or_create = ["user", "email"]
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    email = LazyAttribute(lambda address: address.user.email)
    primary = True
    verified = True
