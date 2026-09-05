"""Populate the LOCAL database with realistic fake data at a chosen scale.

    manage.py seed_db --scale 0.5 --seed 42   # ~3.2k users
    manage.py seed_db --scale 1 --seed 42     # 1,000,000 users

Every run is deterministic (--seed is required) and starts from an empty
@seed.example.com domain: previously seeded rows are wiped first, so the
result of a given (scale, seed) pair is always the same database.

Scale is logarithmic: rows = 10 * 100_000**scale (0 -> 10, 0.5 -> ~3.2k,
0.75 -> ~56k, 1.0 -> 1M) - that is the USER count; the seeder populates the
whole domain graph per user (email address, wallet, payments with a
realistic status mix, a replayable wallet ledger, saved cards, devices,
notifications with per-channel delivery rows) plus broadcasts, whose count
follows the same curve and which alternate between COMPLETED and
mid-dispatch so resume tooling has something to chew on. The realism knobs
live in one place: MIX.

Seeders build instances through the factories WITHOUT saving (factory
build + chunked bulk_create) - per-row save/post_generation would take
hours at scale 1. Related rows fan out per parent with variance via
fan_out(); a future AddressSeeder just declares per_parent=(0, 10).
"""

import math
import random
import time
import uuid
from collections import Counter
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser
from django.db import connection
from django.db.models import F
from django.db.models import Model
from django.db.models.expressions import RawSQL
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import DevicePlatform
from apps.notifications.constants import NotificationKind
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from config.env import env

SEED_DOMAIN = "seed.example.com"
CHUNK = 10_000
BATCH = 1_000

# The executor's skip reasons (apps/notifications/services/execution.py writes
# these literals into NotificationDelivery.detail); the bulk path replicates
# its rows, so the strings must match what an operator sees for real skips.
SKIP_NO_DEVICES = "no devices"
SKIP_NO_PHONE = "no phone"

# Every realism knob of the generated graph. Shares are probabilities per
# row; (lo, hi) pairs are uniform per-parent fan-out ranges.
MIX: dict[str, Any] = {
    "payments_per_user": (0, 4),
    "payment_amount": (10, 500),
    "topup_share": 0.7,  # payments that are wallet top-ups (vs. PaymentKind.OTHER)
    "payment_status": (  # cumulative weights, in order
        (PaymentStatus.PAID, 0.65),
        (PaymentStatus.PENDING, 0.15),
        (PaymentStatus.FAILED, 0.12),
        (PaymentStatus.REFUNDED, 0.08),
    ),
    "spend_share": 0.4,  # users with credit who spent part of it
    "announcement_share": 0.1,  # users holding a one-off announcement
    "read_share": 0.6,  # notifications already read
    "push_delivered_share": 0.9,  # push deliveries that succeeded (rest FAILED)
    "saved_cards_per_user": (0, 2),
    "devices_per_user": (0, 2),
    "card_brands": ("VISA", "MASTERCARD", "MADA"),
    "broadcast_audience_cap": 1_000,  # recipients per broadcast, at most
}


def target_count(scale: float) -> int:
    return round(10 * math.pow(100_000, scale))


def broadcast_count(scale: float) -> int:
    """One broadcast per order of magnitude of users, plus one (0 -> 2)."""
    return round(math.log10(target_count(scale))) + 1


def weighted_choice[T](weights: tuple[tuple[T, float], ...], rng: random.Random) -> T:
    roll = rng.random()
    cumulative = 0.0
    for value, weight in weights:
        cumulative += weight
        if roll < cumulative:
            return value
    return weights[-1][0]


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
            "--seed",
            type=int,
            required=True,
            help="RNG seed: the same (scale, seed) always yields the same data.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if env.ENVIRONMENT != "local":
            msg = f"seed_db only runs locally (ENVIRONMENT={env.ENVIRONMENT})."
            raise CommandError(msg)
        scale: float = options["scale"]
        if not 0 <= scale <= 1:
            msg = f"--scale must be within 0..1, got {scale}."
            raise CommandError(msg)
        seed: int = options["seed"]

        # Dev-only deps (factory_boy, mimesis): import at run time so the
        # module stays importable in production images.
        from factory.random import reseed_random

        from apps.common.tests import fake

        rng = random.Random(seed)  # noqa: S311 - fake data, not crypto
        fake.reseed(seed)
        # factory_boy's own RNG (FuzzyChoice); untyped upstream
        reseed_random(seed)  # type: ignore[no-untyped-call]

        self.stdout.write(f"wiped {self._wipe():,} previously seeded rows")
        count = target_count(scale)
        self.stdout.write(f"scale={scale} -> {count:,} users")
        started = time.monotonic()
        totals = self._seed_users(count, rng)
        totals.update(self._seed_broadcasts(broadcast_count(scale), rng))
        elapsed = time.monotonic() - started

        for label, rows in totals.items():
            self.stdout.write(f"  {label}: {rows:,} rows")
        rows = sum(totals.values())
        self.stdout.write(
            self.style.SUCCESS(
                f"seeded {rows:,} rows in {elapsed:.1f}s ({rows / elapsed:,.0f} rows/s)"
            )
        )

    def _wipe(self) -> int:
        from apps.notifications.models import Broadcast
        from apps.payments.models import Payment
        from apps.payments.models import SavedCard
        from apps.payments.models import Wallet
        from apps.payments.models import WalletTransaction
        from apps.users.models import User

        seeded_users = User.objects.filter(email__endswith=f"@{SEED_DOMAIN}")
        # PROTECT chain dictates the order: ledger rows first (they protect
        # wallets AND payments), then payments, then saved cards (after
        # payments so SET_NULL never rewrites doomed rows), then wallets,
        # then broadcasts (created_by is PROTECT and their notifications /
        # deliveries cascade with them); deleting users last cascades the
        # rest (EmailAddress, Device, Notification, NotificationDelivery).
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
        return wiped

    def _seed_users(self, count: int, rng: random.Random) -> Counter[str]:
        from allauth.account.models import EmailAddress

        from apps.users.models import User
        from apps.users.tests.factories import UserFactory

        totals: Counter[str] = Counter()
        for chunk_start in range(0, count, CHUNK):
            size = min(CHUNK, count - chunk_start)
            # Query counts make bulk-only violations visible instantly: a
            # seeder step that slips into per-row saves shows up as a
            # query-count explosion, not just unexplained slowness.
            # (CaptureQueriesContext forces the cursor to record regardless
            # of DEBUG.)
            with CaptureQueriesContext(connection) as queries:
                users = UserFactory.build_bulk(
                    emails=[
                        f"user{chunk_start + i}@{SEED_DOMAIN}" for i in range(size)
                    ],
                    rng=rng,
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
            totals.update(graph_counts)
            self.stdout.write(
                f"  users {totals['users']:,}/{count:,} ({len(queries)} queries)"
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
        (_on_paid, user_create) would have sent with catalog-complete
        contexts, and delivery rows replicate what the pipeline would have
        recorded (the bulk path bypasses services, so it must copy their
        output shape).
        """
        from apps.notifications.catalog import CATALOG
        from apps.notifications.models import Device
        from apps.notifications.models import Notification
        from apps.notifications.models import NotificationDelivery
        from apps.payments.models import Payment
        from apps.payments.models import SavedCard
        from apps.payments.models import Wallet
        from apps.payments.models import WalletTransaction
        from apps.payments.services import wallet_currency_for
        from apps.users.constants import Language

        now = timezone.now()
        # (payment-or-None, kind, signed amount, balance_after)
        type TxSpec = tuple[Any, WalletTransactionKind, Decimal, Decimal]
        # (kind, catalog-complete context)
        type NotifSpec = tuple[NotificationKind, dict[str, str]]
        payments: list[Any] = []
        plans: list[tuple[Any, str, Decimal, list[TxSpec], list[NotifSpec]]] = []
        for user in users:
            # Signup provisions the wallet in the language's currency; every
            # payment of a seeded user is in that currency.
            currency = str(wallet_currency_for(language=Language(user.language)))
            balance = Decimal(0)
            tx_specs: list[TxSpec] = []
            # Signup writes the WELCOME inbox row (user_create).
            notif_specs: list[NotifSpec] = [
                (NotificationKind.WELCOME, {"name": user.name})
            ]
            for _ in range(rng.randint(*MIX["payments_per_user"])):
                amount = Decimal(rng.randint(*MIX["payment_amount"]))
                is_topup = rng.random() < MIX["topup_share"]
                status = weighted_choice(MIX["payment_status"], rng)
                settled = status in (PaymentStatus.PAID, PaymentStatus.REFUNDED)
                payment = Payment(
                    user=user,
                    amount=amount,
                    currency=currency,
                    kind=PaymentKind.WALLET_TOPUP if is_topup else PaymentKind.OTHER,
                    status=status,
                    gateway=GatewayName.TAP,
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
            if balance > 0 and rng.random() < MIX["spend_share"]:
                spend = Decimal(rng.randint(1, int(balance)))
                balance -= spend
                tx_specs.append((None, WalletTransactionKind.PAYMENT, -spend, balance))
            if rng.random() < MIX["announcement_share"]:
                notif_specs.append(
                    (
                        NotificationKind.ANNOUNCEMENT,
                        {
                            "title_ar": "إعلان تجريبي",
                            "title_en": "Seed announcement",
                            "message_ar": f"إعلان تجريبي {uuid.uuid4().hex[:6]}",
                            "message_en": f"Seed announcement {uuid.uuid4().hex[:6]}",
                        },
                    )
                )
            plans.append((user, currency, balance, tx_specs, notif_specs))

        Payment.objects.bulk_create(payments, batch_size=BATCH)
        # Signup invariant (UserFactory.wallet RelatedFactory): one wallet per
        # user - carrying the ledger's final balance.
        wallets = [
            Wallet(user=user, currency=currency, balance=balance)
            for user, currency, balance, _, _ in plans
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
            for wallet, (_, _, _, tx_specs, _) in zip(wallets, plans, strict=True)
            for payment, kind, amount, after in tx_specs
        ]
        WalletTransaction.objects.bulk_create(transactions, batch_size=BATCH)
        # SavedCardFactory has no post_generation hooks - plain field parity
        # is the whole contract (unique (gateway, token) via uuid).
        saved_cards = fan_out(
            users,
            per_parent=MIX["saved_cards_per_user"],
            build_child=lambda user: SavedCard(
                user=user,
                gateway=GatewayName.TAP,
                token=f"fake_card_seed_{uuid.uuid4().hex}",
                gateway_customer_id=f"fake_cus_seed_{uuid.uuid4().hex[:12]}",
                gateway_agreement_id=f"fake_agr_seed_{uuid.uuid4().hex[:12]}",
                brand=rng.choice(MIX["card_brands"]),
                last4=f"{rng.randint(0, 9999):04d}",
                exp_month=rng.randint(1, 12),
                exp_year=now.year + rng.randint(1, 5),
            ),
            rng=rng,
        )
        SavedCard.objects.bulk_create(saved_cards, batch_size=BATCH)
        devices = fan_out(
            users,
            per_parent=MIX["devices_per_user"],
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
                read_at=now if rng.random() < MIX["read_share"] else None,
            )
            for user, _, _, _, notif_specs in plans
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
                    delivered = rng.random() < MIX["push_delivered_share"]
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
                            detail=SKIP_NO_DEVICES
                            if channel == Channel.PUSH
                            else SKIP_NO_PHONE,
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

    def _seed_broadcasts(self, count: int, rng: random.Random) -> dict[str, int]:
        """Broadcasts over capped random audiences, alternating COMPLETED (the
        happy path in admin) and DISPATCHED with a PENDING remainder (so
        `sweep_deliveries --broadcast` / the Resume action have real work).
        Bulk path: rows replicate exactly what dispatcher + executor write.
        """
        from apps.notifications.models import Broadcast
        from apps.notifications.models import Device
        from apps.notifications.models import Notification
        from apps.notifications.models import NotificationDelivery
        from apps.users.models import User

        population = list(
            User.objects.filter(
                email__endswith=f"@{SEED_DOMAIN}", is_active=True
            ).order_by("pk")[: MIX["broadcast_audience_cap"]]
        )
        now = timezone.now()
        has_device = set(
            Device.objects.filter(user__in=population).values_list("user_id", flat=True)
        )
        counts = {"broadcasts": 0, "notifications": 0, "notification deliveries": 0}
        statuses = (BroadcastStatus.COMPLETED, BroadcastStatus.DISPATCHED)
        for index in range(count):
            status = statuses[index % len(statuses)]
            reached = sorted(
                rng.sample(population, rng.randint(1, len(population))),
                key=lambda user: user.pk,
            )
            # Mid-flight: half the audience dispatched, deliveries pending.
            audience = (
                reached
                if status == BroadcastStatus.COMPLETED
                else reached[: len(reached) // 2 + 1]
            )
            delivery_status = (
                DeliveryStatus.SENT
                if status == BroadcastStatus.COMPLETED
                else DeliveryStatus.PENDING
            )
            broadcast = Broadcast(
                kind=NotificationKind.ANNOUNCEMENT,
                context={
                    "title_ar": "بث تجريبي",
                    "title_en": "Seed broadcast",
                    "message_ar": f"بث تجريبي {uuid.uuid4().hex[:6]}",
                    "message_en": f"Seed broadcast {uuid.uuid4().hex[:6]}",
                },
                status=status,
                created_by=population[0],
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
                total_recipients=len(reached),
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
