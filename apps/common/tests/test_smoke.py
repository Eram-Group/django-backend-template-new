"""Plumbing smoke: fixtures, xdist, and coverage wiring all function."""

import pytest
from allauth.account.models import EmailAddress
from django.test import RequestFactory

from apps.common.health import healthz
from apps.common.health import readyz
from apps.users.models import User


def test_healthz_is_alive(rf: RequestFactory) -> None:
    response = healthz(rf.get("/healthz"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_readyz_checks_database_and_cache(rf: RequestFactory) -> None:
    response = readyz(rf.get("/readyz"))
    assert response.status_code == 200


def test_user_fixture_is_verified_and_passwordless(user: User) -> None:
    assert user.pk is not None
    assert not user.has_usable_password()
    assert EmailAddress.objects.filter(user=user, verified=True, primary=True).exists()
