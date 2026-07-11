"""Project-wide pytest fixtures."""

from pathlib import Path

import pytest
from allauth.account.models import EmailAddress

from apps.users.models import User


@pytest.fixture(autouse=True)
def _tmp_media_root(settings: object, tmp_path: Path) -> None:
    """Tests never write into the real media/ directory."""
    settings.MEDIA_ROOT = tmp_path / "media"  # type: ignore[attr-defined]


@pytest.fixture
def user(db: None) -> User:
    """A regular, passwordless user with a VERIFIED email.

    The verified EmailAddress row matters: with mandatory email
    verification, login flows pend on verify_email without it.
    """
    created = User.objects.create_user("user@example.com", name="Test User")
    EmailAddress.objects.create(
        user=created, email=created.email, primary=True, verified=True
    )
    return created
