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
    # The message is localized (Arabic-first); clients branch on the code.
    with pytest.raises(UserNotFoundError) as excinfo:
        selectors.user_get(pk=uuid.uuid4())
    assert excinfo.value.status_code == 404
    assert excinfo.value.code == "user_not_found"


def test_user_list_is_every_user() -> None:
    active = UserFactory.create()
    inactive = UserFactory.create(is_active=False)

    assert {active, inactive} <= set(selectors.user_list())
