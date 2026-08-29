"""seed_db smoke: the documented daily command (`just seed`) must work.

The bulk path (build + bulk_create, wipe-by-domain, the local-only guard)
never ran in CI before this - seeder regressions shipped green.
"""

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.notifications.constants import BroadcastStatus
from apps.notifications.models import Broadcast
from apps.payments.models import Payment
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.users.models import User
from apps.users.tests.factories import UserFactory
from config.env import env

pytestmark = pytest.mark.django_db

SEED_SUFFIX = "@seed.example.com"


def test_scale_zero_seeds_ten_users_and_reruns_start_from_a_wiped_domain() -> None:
    survivor = UserFactory.create()

    call_command("seed_db", scale=0, seed=42)
    assert User.objects.filter(email__endswith=SEED_SUFFIX).count() == 10

    # A rerun wipes previously seeded rows (and only them), then reseeds.
    call_command("seed_db", scale=0, seed=42)
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
    assert seeded.exclude(name="").count() == seeded.count()

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


def test_broadcasts_follow_the_curve_and_alternate_statuses() -> None:
    call_command("seed_db", scale=0, seed=7)

    seeded = Broadcast.objects.filter(created_by__email__endswith=SEED_SUFFIX)
    statuses = sorted(seeded.values_list("status", flat=True))
    assert statuses == [BroadcastStatus.COMPLETED, BroadcastStatus.DISPATCHED]


def test_same_seed_is_deterministic() -> None:
    call_command("seed_db", scale=0, seed=42)
    first = sorted(User.objects.filter(email__endswith=SEED_SUFFIX).values_list("name"))
    call_command("seed_db", scale=0, seed=42)
    second = sorted(
        User.objects.filter(email__endswith=SEED_SUFFIX).values_list("name")
    )
    assert first == second


def test_seed_is_required() -> None:
    with pytest.raises(CommandError, match="--seed"):
        call_command("seed_db", scale=0)


def test_guard_refuses_outside_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(env, "ENVIRONMENT", "production")
    with pytest.raises(CommandError, match="only runs locally"):
        call_command("seed_db", scale=0, seed=1)


def test_scale_outside_range_is_rejected() -> None:
    with pytest.raises(CommandError, match=r"within 0\.\.1"):
        call_command("seed_db", scale=1.5, seed=1)
