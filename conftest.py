"""Project-wide pytest fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import stamina
from pytest_django.fixtures import SettingsWrapper

from apps.users.models import User
from apps.users.tests.factories import UserFactory


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
def _tmp_media_root(settings: SettingsWrapper, tmp_path: Path) -> None:
    """Tests never write into the real media/ directory."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def user(db: None) -> User:
    """A regular, passwordless user with a VERIFIED email.

    UserFactory owns that invariant (verified primary EmailAddress via
    post_generation, unusable password) - single source of truth.
    """
    return UserFactory.create()
