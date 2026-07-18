import uuid

import pytest

from apps.users import selectors
from apps.users.exceptions import UserNotFoundError
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_user_get_returns_the_user() -> None:
    user = UserFactory.create()
    assert selectors.user_get(pk=user.pk) == user


def test_user_get_unknown_pk_raises_404_domain_error() -> None:
    with pytest.raises(UserNotFoundError, match="User not found") as excinfo:
        selectors.user_get(pk=uuid.uuid4())
    assert excinfo.value.status_code == 404


def test_user_list_filters_by_is_active() -> None:
    active = UserFactory.create()
    inactive = UserFactory.create(is_active=False)

    everyone = selectors.user_list()
    assert {active, inactive} <= set(everyone)

    active_only = set(selectors.user_list(is_active=True))
    assert active in active_only
    assert inactive not in active_only

    inactive_only = set(selectors.user_list(is_active=False))
    assert inactive in inactive_only
    assert active not in inactive_only
