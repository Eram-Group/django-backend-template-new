from factory.declarations import Sequence
from factory.django import DjangoModelFactory
from factory.faker import Faker

from apps.users.models import User


class UserFactory(DjangoModelFactory[User]):
    class Meta:
        model = User
        django_get_or_create = ["email"]
        skip_postgeneration_save = True

    email = Sequence(lambda n: f"user{n}@example.com")
    name = Faker("name")
