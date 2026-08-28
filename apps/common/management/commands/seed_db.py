"""Populate the LOCAL database with realistic fake data at a chosen scale.

    manage.py seed_db --scale 0.5           # ~3.2k users
    manage.py seed_db --scale 1             # 1,000,000 users
    manage.py seed_db --scale 0.3 --seed 42 # deterministic run
    manage.py seed_db --wipe --scale 0      # wipe old seed data, tiny reseed

Scale is logarithmic: rows = 10 * 100_000**scale (0 -> 10, 0.5 -> ~3.2k,
0.75 -> ~56k, 1.0 -> 1M) - that is the USER count; the seeder populates the
whole domain graph per user (email address, wallet, payments with a
realistic status mix, a replayable wallet ledger, saved cards, devices,
notifications with per-channel delivery rows), so total
rows are ~8-10x, plus two sample broadcasts (one completed, one mid-dispatch
so resume tooling has something to chew on). Seeded rows carry the
@seed.example.com email domain so --wipe can remove exactly them.

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
from django.core.management.base import CommandParser
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


def fan_out[P, C: Model](
    parents: list[P],
    *,
    per_parent: tuple[int, int],
    build_child: Callable[[P], C],
    rng: random.Random,
) -> list[C]:
    """Children per parent drawn uniformly from per_parent=(lo, hi)."""
    return [
        build_child(parent)
        for parent in parents
        for _ in range(rng.randint(*per_parent))
    ]


class Command(BaseCommand):
    help = "Seed the local database with realistic fake data (--scale 0..1)."

    def add_arguments(self, parser: CommandParser) -> None:
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
        from factory.random import reseed_random

        from apps.common.tests import fake
        from apps.users.models import User

        rng = random.Random(options["seed"])  # noqa: S311 - fake data, not crypto
        if options["seed"] is not None:
            random.seed(options["seed"])
            fake.reseed(options["seed"])
            # factory_boy's own RNG (FuzzyChoice); untyped upstream
            reseed_random(options["seed"])  # type: ignore[no-untyped-call]

        if options["wipe"]:
            from apps.notifications.models import Broadcast
            from apps.payments.models import Payment
            from apps.payments.models import SavedCard
            from apps.payments.models import Wallet
            from apps.payments.models import WalletTransaction

            seeded_users = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}")
            # PROTECT chain dictates the order: ledger rows first (they
            # protect wallets AND payments), then payments, then saved cards
            # (after payments so SET_NULL never rewrites doomed rows), then
            # wallets, then broadcasts (created_by is PROTECT and their
            # notifications/deliveries cascade with them); deleting users
            # last cascades the rest (EmailAddress, Device, Notification,
            # NotificationDelivery).
            wiped = 0
            for qs in (
                WalletTransaction.objects.filter(wallet__user__in=seeded_users),
                Payment.objects.filter(user__in=seeded_users),
                SavedCard.objects.filter(user__in=seeded_users),
                Wallet.objects.filter(user__in=seeded_users),
                Broadcast.objects.filter(created_by__in=seeded_users),
                seeded_users,
            ):
                deleted, _ = qs.delete()
                wiped += deleted
            self.stdout.write(f"wiped {wiped} previously seeded rows")

        count = target_count(scale)
        self.stdout.write(f"scale={scale} -> {count:,} users")
        started = time.monotonic()
        totals = self._seed_users(count, rng)
        for label, rows in self._seed_broadcasts(rng).items():
            totals[label] = totals.get(label, 0) + rows
        elapsed = time.monotonic() - started

        for label, rows in totals.items():
            self.stdout.write(f"  {label}: {rows:,} rows")
        rate = sum(totals.values()) / max(elapsed, 0.001)
        self.stdout.write(
            self.style.SUCCESS(f"seeded in {elapsed:.1f}s ({rate:,.0f} rows/s)")
        )

    def _seed_users(self, count: int, rng: random.Random) -> dict[str, int]:
        from allauth.account.models import EmailAddress

        from apps.common.tests import fake
        from apps.users.models import User
        from apps.users.tests.factories import LANGUAGE_WEIGHTS

        offset = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}").count()
        totals: dict[str, int] = {"users": 0, "email addresses": 0}
        started = time.monotonic()
        for chunk_start in range(0, count, CHUNK):
            size = min(CHUNK, count - chunk_start)
            # Query counts make bulk-only violations visible instantly: a
            # seeder step that slips into per-row saves shows up as a
            # query-count explosion, not just unexplained slowness.
            # (CaptureQueriesContext forces the cursor to record regardless
            # of DEBUG.)
            with CaptureQueriesContext(connection) as queries:
                # Plain constructors, not UserFactory.build: factory
                # declaration resolution measured ~9x slower per instance,
                # and this loop dominates seeding CPU. Field parity with
                # UserFactory (weighted language, locale-matched name,
                # unusable password) is deliberate - keep them in sync.
                users = []
                for i in range(size):
                    language = rng.choice(LANGUAGE_WEIGHTS)
                    users.append(
                        User(
                            email=f"user{offset + chunk_start + i}@{SEED_DOMAIN}",
                            language=language,
                            name=fake.full_name(language),
                            password="!",  # noqa: S106 - unusable marker
                        )
                    )
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
                graph_counts = self._seed_domain_graph(created, rng)
            totals["users"] += len(created)
            totals["email addresses"] += len(addresses)
            for label, rows in graph_counts.items():
                totals[label] = totals.get(label, 0) + rows
            rate = totals["users"] / max(time.monotonic() - started, 0.001)
            self.stdout.write(
                f"  users {totals['users']:,}/{count:,} "
                f"({rate:,.0f}/s, {len(queries)} queries)"
            )

        # Spread signup timestamps over the past year (all-identical
        # created_at is a giveaway); update() bypasses auto_now*. Second
        # statement is needed: F() reads the pre-update column value.
        seeded = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}")
        seeded.update(created_at=RawSQL("now() - (random() * interval '365 days')", []))
        seeded.update(updated_at=F("created_at"), date_joined=F("created_at"))
        self._spread_child_timestamps()
        return totals

    def _seed_domain_graph(
        self, users: list[Any], rng: random.Random
    ) -> dict[str, int]:
        """Payments, a replayable wallet ledger, saved cards, devices, and
        notifications for one chunk of freshly created users - all bulk, all
        invariants: wallet.balance equals the last ledger balance_after (and
        never goes negative), notifications mirror what the producers
        (_on_paid, user_post_signup) would have sent with catalog-complete
        contexts, and delivery rows replicate what the pipeline would have
        recorded (the bulk path bypasses services, so it must copy their
        output shape).
        """
        import uuid
        from decimal import Decimal

        from django.utils import timezone

        from apps.notifications.catalog import CATALOG
        from apps.notifications.constants import Channel
        from apps.notifications.constants import DeliveryStatus
        from apps.notifications.constants import DevicePlatform
        from apps.notifications.constants import NotificationKind
        from apps.notifications.models import Device
        from apps.notifications.models import Notification
        from apps.notifications.models import NotificationDelivery
        from apps.payments.constants import DEFAULT_CURRENCY
        from apps.payments.constants import GatewayName
        from apps.payments.constants import PaymentKind
        from apps.payments.constants import PaymentStatus
        from apps.payments.constants import WalletTransactionKind
        from apps.payments.models import Payment
        from apps.payments.models import SavedCard
        from apps.payments.models import Wallet
        from apps.payments.models import WalletTransaction

        now = timezone.now()
        currency = str(DEFAULT_CURRENCY)
        # (payment-or-None, kind, signed amount, balance_after)
        type TxSpec = tuple[Any, WalletTransactionKind, Decimal, Decimal]
        # (kind, catalog-complete context)
        type NotifSpec = tuple[NotificationKind, dict[str, str]]
        payments: list[Any] = []
        plans: list[tuple[Any, Decimal, list[TxSpec], list[NotifSpec]]] = []
        for user in users:
            balance = Decimal(0)
            tx_specs: list[TxSpec] = []
            # Signup writes the WELCOME inbox row (user_post_signup).
            notif_specs: list[NotifSpec] = [
                (NotificationKind.WELCOME, {"name": user.name or user.email})
            ]
            for _ in range(rng.randint(0, 4)):
                amount = Decimal(rng.randint(10, 500))
                is_topup = rng.random() < 0.7
                # PAID .65 / PENDING .15 / FAILED .12 / rest REFUNDED
                roll = rng.random()
                if roll < 0.65:
                    status = PaymentStatus.PAID
                elif roll < 0.80:
                    status = PaymentStatus.PENDING
                elif roll < 0.92:
                    status = PaymentStatus.FAILED
                else:
                    status = PaymentStatus.REFUNDED
                settled = status in (PaymentStatus.PAID, PaymentStatus.REFUNDED)
                payment = Payment(
                    user=user,
                    amount=amount,
                    currency=currency,
                    kind=PaymentKind.WALLET_TOPUP if is_topup else PaymentKind.OTHER,
                    status=status,
                    gateway=GatewayName.FAKE,
                    gateway_charge_id=f"fake_charge_seed_{uuid.uuid4().hex}",
                    paid_at=now if settled else None,
                )
                payments.append(payment)
                if settled and is_topup:
                    balance += amount
                    tx_specs.append(
                        (payment, WalletTransactionKind.TOPUP, amount, balance)
                    )
                    # Mirrors _on_paid's WALLET_CREDITED context exactly.
                    notif_specs.append(
                        (
                            NotificationKind.WALLET_CREDITED,
                            {
                                "amount": str(amount),
                                "currency": currency,
                                "balance": str(balance),
                            },
                        )
                    )
                elif settled:
                    notif_specs.append(
                        (
                            NotificationKind.PAYMENT_PAID,
                            {"amount": str(amount), "currency": currency},
                        )
                    )
                if status == PaymentStatus.REFUNDED and is_topup:
                    balance -= amount
                    tx_specs.append(
                        (payment, WalletTransactionKind.REFUND, -amount, balance)
                    )
            if balance > 0 and rng.random() < 0.4:  # user spent part of the credit
                spend = Decimal(rng.randint(1, int(balance)))
                balance -= spend
                tx_specs.append((None, WalletTransactionKind.PAYMENT, -spend, balance))
            if rng.random() < 0.1:
                notif_specs.append(
                    (
                        NotificationKind.ANNOUNCEMENT,
                        {
                            "title": "Seed announcement",
                            "message": f"Seed announcement {uuid.uuid4().hex[:6]}",
                        },
                    )
                )
            plans.append((user, balance, tx_specs, notif_specs))

        Payment.objects.bulk_create(payments, batch_size=BATCH)
        # Signup invariant (UserFactory.wallet RelatedFactory): one wallet per
        # user - carrying the ledger's final balance.
        wallets = [
            Wallet(user=user, currency=currency, balance=balance)
            for user, balance, _, _ in plans
        ]
        Wallet.objects.bulk_create(wallets, batch_size=BATCH)
        transactions = [
            WalletTransaction(
                wallet=wallet,
                kind=kind,
                amount=amount,
                balance_after=after,
                payment=payment,
            )
            for wallet, (_, _, tx_specs, _) in zip(wallets, plans, strict=True)
            for payment, kind, amount, after in tx_specs
        ]
        WalletTransaction.objects.bulk_create(transactions, batch_size=BATCH)
        # SavedCardFactory has no post_generation hooks - plain field parity
        # is the whole contract (unique (gateway, token) via uuid).
        saved_cards = fan_out(
            users,
            per_parent=(0, 2),
            build_child=lambda user: SavedCard(
                user=user,
                gateway=GatewayName.FAKE,
                token=f"fake_card_seed_{uuid.uuid4().hex}",
                gateway_customer_id=f"fake_cus_seed_{uuid.uuid4().hex[:12]}",
                gateway_agreement_id=f"fake_agr_seed_{uuid.uuid4().hex[:12]}",
                brand=rng.choice(["VISA", "MASTERCARD", "MADA"]),
                last4=f"{rng.randint(0, 9999):04d}",
                exp_month=rng.randint(1, 12),
                exp_year=now.year + rng.randint(1, 5),
            ),
            rng=rng,
        )
        SavedCard.objects.bulk_create(saved_cards, batch_size=BATCH)
        devices = fan_out(
            users,
            per_parent=(0, 2),
            build_child=lambda user: Device(
                user=user,
                registration_id=f"seed-tok-{uuid.uuid4().hex}",
                platform=rng.choice(DevicePlatform.values),
            ),
            rng=rng,
        )
        Device.objects.bulk_create(devices, batch_size=BATCH)
        has_device = {device.user_id for device in devices}
        notifications = [
            Notification(
                recipient=user,
                kind=kind,
                context=context,
                read_at=now if rng.random() < 0.6 else None,
            )
            for user, _, _, notif_specs in plans
            for kind, context in notif_specs
        ]
        Notification.objects.bulk_create(notifications, batch_size=BATCH)
        # Replicates notification_send + the executor: one delivery row per
        # catalog-SEED channel (the seeder never reads operator-edited config
        # rows - fresh DBs are identical either way), SENT when the user has a
        # device, SKIPPED otherwise, with a sprinkle of FAILED for realism.
        deliveries = []
        for notification in notifications:
            entry = CATALOG[NotificationKind(notification.kind)]
            for channel in sorted(entry.default_channels):
                if channel == Channel.PUSH and notification.recipient_id in has_device:
                    delivered = rng.random() < 0.9
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=channel,
                            status=DeliveryStatus.SENT
                            if delivered
                            else DeliveryStatus.FAILED,
                            sent_at=now if delivered else None,
                            detail="" if delivered else "seed: provider rejection",
                        )
                    )
                else:  # no capability - the executor records the skip
                    deliveries.append(
                        NotificationDelivery(
                            notification=notification,
                            channel=channel,
                            status=DeliveryStatus.SKIPPED,
                            detail="no devices"
                            if channel == Channel.PUSH
                            else "no phone",
                        )
                    )
        NotificationDelivery.objects.bulk_create(deliveries, batch_size=BATCH)
        return {
            "payments": len(payments),
            "wallets": len(wallets),
            "wallet transactions": len(transactions),
            "saved cards": len(saved_cards),
            "devices": len(devices),
            "notifications": len(notifications),
            "notification deliveries": len(deliveries),
        }

    def _seed_broadcasts(self, rng: random.Random) -> dict[str, int]:
        """Two sample broadcasts over a capped user sample: one COMPLETED
        (the happy path in admin), one DISPATCHED with PENDING remainder (so
        `sweep_deliveries --broadcast` / the Resume action have real work).
        Bulk path: rows replicate exactly what dispatcher + executor write.
        """
        import uuid

        from django.utils import timezone

        from apps.notifications.constants import BroadcastStatus
        from apps.notifications.constants import Channel
        from apps.notifications.constants import DeliveryStatus
        from apps.notifications.constants import NotificationKind
        from apps.notifications.models import Broadcast
        from apps.notifications.models import Device
        from apps.notifications.models import Notification
        from apps.notifications.models import NotificationDelivery
        from apps.users.models import User

        sample = list(
            User.objects.filter(
                email__endswith=f"@{SEED_DOMAIN}", is_active=True
            ).order_by("pk")[:1000]
        )
        if not sample:
            return {}
        now = timezone.now()
        has_device = set(
            Device.objects.filter(user__in=sample).values_list("user_id", flat=True)
        )
        counts = {"broadcasts": 0, "notifications": 0, "notification deliveries": 0}
        specs = (
            (BroadcastStatus.COMPLETED, sample, DeliveryStatus.SENT),
            # Mid-flight: half the audience dispatched, deliveries pending.
            (
                BroadcastStatus.DISPATCHED,
                sample[: max(1, len(sample) // 2)],
                DeliveryStatus.PENDING,
            ),
        )
        for status, audience, delivery_status in specs:
            broadcast = Broadcast(
                kind=NotificationKind.ANNOUNCEMENT,
                context={
                    "title": "Seed broadcast",
                    "message": f"Seed broadcast {uuid.uuid4().hex[:6]}",
                },
                status=status,
                created_by=sample[0],
                dispatch_cursor=audience[-1].pk,
            )
            broadcast.save()
            notifications = [
                Notification(
                    recipient=user,
                    kind=NotificationKind.ANNOUNCEMENT,
                    context=broadcast.context,
                    broadcast=broadcast,
                )
                for user in audience
            ]
            Notification.objects.bulk_create(notifications, batch_size=BATCH)
            deliveries = [
                NotificationDelivery(
                    notification=notification,
                    broadcast=broadcast,
                    channel=Channel.PUSH,
                    status=delivery_status,
                    sent_at=now if delivery_status == DeliveryStatus.SENT else None,
                )
                for notification in notifications
                if notification.recipient_id in has_device
            ]
            NotificationDelivery.objects.bulk_create(deliveries, batch_size=BATCH)
            sent = len(deliveries) if delivery_status == DeliveryStatus.SENT else 0
            Broadcast.objects.filter(pk=broadcast.pk).update(
                total_recipients=len(audience),
                total_deliveries=len(deliveries),
                sent_count=sent,
                updated_at=now,
            )
            counts["broadcasts"] += 1
            counts["notifications"] += len(notifications)
            counts["notification deliveries"] += len(deliveries)
        return counts

    def _spread_child_timestamps(self) -> None:
        """Give payments, saved cards, devices, and notifications past-dated
        created_at too.

        The ledger and wallets keep insertion time on purpose: the per-wallet
        balance_after chain must stay chronologically replayable, and a
        random spread would scramble it. Delivery rows keep insertion time as
        well - a delivery predating its notification would read as corrupt.
        """
        from apps.notifications.models import Device
        from apps.notifications.models import Notification
        from apps.payments.models import Payment
        from apps.payments.models import SavedCard

        spread = RawSQL("now() - (random() * interval '180 days')", [])
        seeded_payments = Payment.objects.filter(
            user__email__endswith=f"@{SEED_DOMAIN}"
        )
        seeded_payments.update(created_at=spread)
        seeded_payments.update(updated_at=F("created_at"))
        for model, user_field in (
            (Device, "user"),
            (Notification, "recipient"),
            (SavedCard, "user"),
        ):
            rows = model.objects.filter(
                **{f"{user_field}__email__endswith": f"@{SEED_DOMAIN}"}
            )
            rows.update(created_at=spread)
            rows.update(updated_at=F("created_at"))
