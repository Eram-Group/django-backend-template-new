"""Fixtures for the admin basics gate.

Data is seeded ONCE per session through the factory registry (committed
outside test transactions, so every parametrized test sees it), plus three
known accounts: a superuser, a permissionless staff user (sidebar
consistency), and a fully-permissioned non-superuser staff user (the
privilege-escalation perspective for the tamper check).
"""

from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.contrib.auth.models import Permission
from django.test import Client

from apps.common.tests.factories_registry import FACTORIES
from apps.users.models import User
from apps.users.tests.factories import UserFactory

SUPERUSER_EMAIL = "gate.superuser@example.com"
PERMLESS_STAFF_EMAIL = "gate.permless.staff@example.com"
PRIV_STAFF_EMAIL = "gate.priv.staff@example.com"


@pytest.fixture(scope="session", autouse=True)
def _gate_data(django_db_setup: Any, django_db_blocker: Any) -> None:
    with django_db_blocker.unblock():
        # Third-party admin targets that no local factory owns.
        Group.objects.get_or_create(name="Gate group")
        UserFactory(email=SUPERUSER_EMAIL, is_staff=True, is_superuser=True)
        UserFactory(email=PERMLESS_STAFF_EMAIL, is_staff=True)
        priv_staff = UserFactory.create(email=PRIV_STAFF_EMAIL, is_staff=True)
        priv_staff.user_permissions.set(Permission.objects.all())
        for factory in FACTORIES.values():
            factory.create_batch(3)


@pytest.fixture
def superuser(db: None) -> User:
    return User.objects.get(email=SUPERUSER_EMAIL)


@pytest.fixture
def permless_staff(db: None) -> User:
    return User.objects.get(email=PERMLESS_STAFF_EMAIL)


@pytest.fixture
def su_client(superuser: User) -> Client:
    client = Client(raise_request_exception=True)
    client.force_login(superuser)
    return client


@pytest.fixture
def staff_client(permless_staff: User) -> Client:
    client = Client(raise_request_exception=True)
    client.force_login(permless_staff)
    return client


@pytest.fixture
def priv_staff(db: None) -> User:
    return User.objects.get(email=PRIV_STAFF_EMAIL)


@pytest.fixture
def priv_staff_client(priv_staff: User) -> Client:
    client = Client(raise_request_exception=True)
    client.force_login(priv_staff)
    return client
