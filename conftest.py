"""Project-wide pytest fixtures."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import stamina
from django.core.cache import cache
from pytest_django import Settings

from apps.notifications.tests.factories import seed_kind_configs
from apps.users.models import User
from apps.users.tests.factories import UserFactory


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup: None, django_db_blocker: Any) -> None:
    """Every kind's NotificationKindConfig row exists from the start, as if
    an operator had saved each card once."""
    with django_db_blocker.unblock():
        seed_kind_configs()


@pytest.fixture(autouse=True, scope="session")
def _stamina_testing() -> Iterator[None]:
    """Outbound-HTTP retries never sleep (or retry) in the suite.

    Tests that exercise retry behaviour re-enable attempts locally with
    ``stamina.set_testing(True, attempts=N)`` - still without sleeping.
    """
    stamina.set_testing(True, attempts=1)
    yield
    stamina.set_testing(False)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Rate-limit counters (allauth, ninja throttles) live in the cache, and
    the test cache is in-process: without this a burst in one test would
    throttle the next."""
    cache.clear()


@pytest.fixture(autouse=True)
def _tmp_media_root(settings: Settings, tmp_path: Path) -> None:
    """Tests never write into the real media/ directory."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def user(db: None) -> User:
    """A regular, passwordless user with a VERIFIED email.

    UserFactory owns that invariant (verified primary EmailAddress via
    post_generation, unusable password) - single source of truth.
    """
    return UserFactory.create()
