"""Populate the LOCAL database with realistic fake data at a chosen scale.

    manage.py seed_db --scale 0.5           # ~3.2k users
    manage.py seed_db --scale 1             # 1,000,000 users
    manage.py seed_db --scale 0.3 --seed 42 # deterministic run
    manage.py seed_db --wipe --scale 0      # wipe old seed data, tiny reseed

Scale is logarithmic: rows = 10 * 100_000**scale (0 -> 10, 0.5 -> ~3.2k,
0.75 -> ~56k, 1.0 -> 1M). Seeded rows carry the @seed.example.com email
domain so --wipe can remove exactly them.

Seeders build instances through the factory registry WITHOUT saving
(factory build + chunked bulk_create) - per-row save/post_generation would
take hours at scale 1. Related rows fan out per parent with variance via
fan_out(); a future AddressSeeder just declares per_parent=(0, 10).
"""

import math
import random
import time
from collections.abc import Callable
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import connection
from django.db.models import F
from django.db.models import Model
from django.db.models.expressions import RawSQL
from django.test.utils import CaptureQueriesContext

from config.env import env

SEED_DOMAIN = "seed.example.com"
CHUNK = 10_000
BATCH = 1_000


def target_count(scale: float) -> int:
    return round(10 * math.pow(100_000, scale))


def fan_out[P](
    parents: list[P],
    per_parent: tuple[int, int],
    build_child: Callable[[P], Model],
    rng: random.Random,
) -> list[Model]:
    """Children per parent drawn uniformly from per_parent=(lo, hi)."""
    return [
        build_child(parent)
        for parent in parents
        for _ in range(rng.randint(*per_parent))
    ]


class Command(BaseCommand):
    help = "Seed the local database with realistic fake data (--scale 0..1)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--scale",
            type=float,
            required=True,
            help="0..1 log curve: 0 -> 10 users, 0.5 -> ~3.2k, 1.0 -> 1,000,000.",
        )
        parser.add_argument(
            "--seed", type=int, default=None, help="Deterministic values."
        )
        parser.add_argument(
            "--wipe",
            action="store_true",
            help=f"First delete all previously seeded rows (@{SEED_DOMAIN}).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if env.ENVIRONMENT != "local":
            msg = f"seed_db only runs locally (ENVIRONMENT={env.ENVIRONMENT})."
            raise CommandError(msg)
        scale: float = options["scale"]
        if not 0 <= scale <= 1:
            msg = f"--scale must be within 0..1, got {scale}."
            raise CommandError(msg)

        # Dev-only deps (factory_boy, mimesis): import at run time so the
        # module stays importable in production images.
        from apps.common.tests import fake
        from apps.users.models import User

        rng = random.Random(options["seed"])  # noqa: S311 - fake data, not crypto
        if options["seed"] is not None:
            random.seed(options["seed"])
            fake.reseed(options["seed"])

        if options["wipe"]:
            deleted, _ = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}").delete()
            self.stdout.write(f"wiped {deleted} previously seeded rows")

        count = target_count(scale)
        self.stdout.write(f"scale={scale} -> {count:,} users")
        started = time.monotonic()
        totals = self._seed_users(count, rng)
        elapsed = time.monotonic() - started

        for label, rows in totals.items():
            self.stdout.write(f"  {label}: {rows:,} rows")
        rate = sum(totals.values()) / max(elapsed, 0.001)
        self.stdout.write(
            self.style.SUCCESS(f"seeded in {elapsed:.1f}s ({rate:,.0f} rows/s)")
        )

    def _seed_users(self, count: int, rng: random.Random) -> dict[str, int]:
        from allauth.account.models import EmailAddress

        from apps.users.models import User
        from apps.users.tests.factories import UserFactory

        offset = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}").count()
        users_done = emails_done = 0
        started = time.monotonic()
        for chunk_start in range(0, count, CHUNK):
            size = min(CHUNK, count - chunk_start)
            # Query counts make bulk-only violations visible instantly: a
            # seeder step that slips into per-row saves shows up as a
            # query-count explosion, not just unexplained slowness.
            # (CaptureQueriesContext forces the cursor to record regardless
            # of DEBUG.)
            with CaptureQueriesContext(connection) as queries:
                users = [
                    UserFactory.build(
                        email=f"user{offset + chunk_start + i}@{SEED_DOMAIN}"
                    )
                    for i in range(size)
                ]
                created = User.objects.bulk_create(users, batch_size=BATCH)
                addresses = fan_out(
                    created,
                    per_parent=(1, 1),  # exactly one verified address per user
                    build_child=lambda user: EmailAddress(
                        user=user, email=user.email, primary=True, verified=True
                    ),
                    rng=rng,
                )
                EmailAddress.objects.bulk_create(addresses, batch_size=BATCH)
            users_done += len(created)
            emails_done += len(addresses)
            rate = users_done / max(time.monotonic() - started, 0.001)
            self.stdout.write(
                f"  users {users_done:,}/{count:,} "
                f"({rate:,.0f}/s, {len(queries)} queries)"
            )

        # Spread signup timestamps over the past year (all-identical
        # created_at is a giveaway); update() bypasses auto_now*. Second
        # statement is needed: F() reads the pre-update column value.
        seeded = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}")
        seeded.update(created_at=RawSQL("now() - (random() * interval '365 days')", []))
        seeded.update(updated_at=F("created_at"), date_joined=F("created_at"))
        return {"users": users_done, "email addresses": emails_done}
