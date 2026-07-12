"""seed_db smoke: the documented daily command (`just seed`) must work.

The bulk path (build + bulk_create, wipe-by-domain, offsets, the local-only
guard) never ran in CI before this - seeder regressions shipped green.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.users.models import User
from apps.users.tests.factories import UserFactory
from config.env import env

pytestmark = pytest.mark.django_db

SEED_SUFFIX = "@seed.example.com"


def test_scale_zero_seeds_ten_users_and_wipe_removes_exactly_them() -> None:
    survivor = UserFactory.create()

    call_command("seed_db", scale=0, seed=42)
    assert User.objects.filter(email__endswith=SEED_SUFFIX).count() == 10

    # --wipe removes previously seeded rows (and only them), then reseeds.
    call_command("seed_db", scale=0, seed=42, wipe=True)
    assert User.objects.filter(email__endswith=SEED_SUFFIX).count() == 10
    assert User.objects.filter(pk=survivor.pk).exists()


def test_reruns_append_with_offset_instead_of_colliding() -> None:
    call_command("seed_db", scale=0)
    call_command("seed_db", scale=0)
    assert User.objects.filter(email__endswith=SEED_SUFFIX).count() == 20


def test_guard_refuses_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "ENVIRONMENT", "production")
    with pytest.raises(CommandError, match="only runs locally"):
        call_command("seed_db", scale=0)


def test_scale_outside_range_is_rejected() -> None:
    with pytest.raises(CommandError, match=r"within 0\.\.1"):
        call_command("seed_db", scale=1.5)
