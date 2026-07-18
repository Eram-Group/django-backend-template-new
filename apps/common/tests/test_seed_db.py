"""seed_db smoke: the documented daily command (`just seed`) must work.

The bulk path (build + bulk_create, wipe-by-domain, offsets, the local-only
guard) never ran in CI before this - seeder regressions shipped green.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.notifications.models import Notification
from apps.payments.models import Payment
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
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
    # The whole graph (payments/ledger incl. PROTECT FKs) wiped cleanly too.
    assert (
        not Payment.objects.exclude(user__email__endswith=SEED_SUFFIX)
        .filter(gateway_charge_id__startswith="fake_charge_seed_")
        .exists()
    )


def test_seeds_the_whole_domain_graph_with_a_consistent_ledger() -> None:
    call_command("seed_db", scale=0.3, seed=42)  # ~316 users

    seeded = User.objects.filter(email__endswith=SEED_SUFFIX)
    assert Wallet.objects.filter(user__in=seeded).count() == seeded.count()
    assert Payment.objects.filter(user__in=seeded).exists()
    assert Notification.objects.filter(recipient__in=seeded).exists()

    # Money invariant: every wallet's balance equals its last ledger
    # balance_after (or zero with an empty ledger), never negative.
    for wallet in Wallet.objects.filter(user__in=seeded):
        assert wallet.balance >= 0
        last = (
            WalletTransaction.objects.filter(wallet=wallet)
            .order_by("-pk")  # uuidv7 pks are insertion-ordered
            .first()
        )
        expected = last.balance_after if last else 0
        assert wallet.balance == expected


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
