"""Data half of the notifications v2 rebuild (schema: 0004, field drops: 0006).

Three backfills, each idempotent and batched:

1. Legacy ``push_sent_at`` / ``sms_sent_at`` markers -> one SENT
   ``NotificationDelivery`` row per (notification, channel). The v2 executor
   claims PENDING rows only, so a marker that is NOT carried over would leave
   no trace of the send at all (nothing re-sends - the old rows simply have no
   delivery record). Reverse copies ``sent_at`` back onto the markers.

2. ``context["title"]`` on announcements authored before the composer.
   ANNOUNCEMENT's catalog title stopped being a fixed gettext string and became
   ``"{title}"``, so its context_keys grew from {"message"} to {"title",
   "message"}. Rows written under the old shape would raise KeyError the next
   time they render (the API renders inbox rows on read, not just at send
   time). The copy is the literal English word, not a translation: this is
   data, and gettext here would freeze whichever locale was active during the
   migration run. Reverse removes only that literal, never an operator's text.

3. One ``NotificationKindConfig`` row per kind, seeded from the catalog values
   live at this migration's birth. Frozen literals, not catalog imports: the
   catalog keeps evolving while this file must replay identically forever.
   English goes into BOTH language columns (every msgstr in the repo's .po
   files is empty, so English is what both locales effectively rendered);
   operators localize in the admin. Historical models have no modeltranslation
   descriptors, so each field writes all three columns explicitly.
"""

from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

BATCH = 500
LEGACY_TITLE = "Announcement"
LEGACY_MARKERS = {"push_sent_at": "push", "sms_sent_at": "sms"}
SEEDS = {
    "welcome": {
        "channels": [],
        "title": "Welcome!",
        "body": "Welcome aboard, {name}!",
    },
    "announcement": {
        # authored_per_send: the composer supplies both halves via context.
        "channels": ["push"],
        "title": "{title}",
        "body": "{message}",
    },
    "payment_paid": {
        "channels": ["push"],
        "title": "Payment received",
        "body": "Your payment of {amount} {currency} was received.",
    },
    "wallet_credited": {
        "channels": ["push"],
        "title": "Wallet credited",
        "body": "{amount} {currency} was added to your wallet. New balance: {balance}.",
    },
}


# --- 1. legacy markers -> delivery rows ---------------------------------------


def _markers_to_deliveries(
    apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor
) -> None:
    notification_model = apps.get_model("notifications", "Notification")
    delivery_model = apps.get_model("notifications", "NotificationDelivery")
    for field, channel in LEGACY_MARKERS.items():
        already = delivery_model.objects.filter(channel=channel).values("notification_id")
        rows = (
            notification_model.objects.exclude(**{field: None})
            .exclude(pk__in=already)
            .values_list("pk", field)
        )
        batch = []
        for pk, sent_at in rows.iterator(chunk_size=BATCH):
            batch.append(
                delivery_model(
                    notification_id=pk,
                    channel=channel,
                    status="sent",
                    sent_at=sent_at,
                    attempts=1,
                )
            )
            if len(batch) >= BATCH:
                delivery_model.objects.bulk_create(batch)
                batch = []
        if batch:
            delivery_model.objects.bulk_create(batch)


def _deliveries_to_markers(
    apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor
) -> None:
    notification_model = apps.get_model("notifications", "Notification")
    delivery_model = apps.get_model("notifications", "NotificationDelivery")
    for field, channel in LEGACY_MARKERS.items():
        rows = (
            delivery_model.objects.filter(
                channel=channel, status__in=["sent", "delivered", "read"]
            )
            .exclude(sent_at=None)
            .values_list("notification_id", "sent_at")
        )
        for notification_id, sent_at in rows.iterator(chunk_size=BATCH):
            notification_model.objects.filter(pk=notification_id).update(
                **{field: sent_at}
            )


# --- 2. announcement title ----------------------------------------------------


def _add_title(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    for label in ("Notification", "Broadcast"):
        model = apps.get_model("notifications", label)
        rows = model.objects.filter(kind="announcement").exclude(
            context__has_key="title"
        )
        batch = []
        for row in rows.iterator(chunk_size=BATCH):
            row.context = {"title": LEGACY_TITLE, **row.context}
            batch.append(row)
            if len(batch) >= BATCH:
                model.objects.bulk_update(batch, ["context"])
                batch = []
        if batch:
            model.objects.bulk_update(batch, ["context"])


def _drop_title(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    for label in ("Notification", "Broadcast"):
        model = apps.get_model("notifications", label)
        rows = model.objects.filter(kind="announcement", context__title=LEGACY_TITLE)
        batch = []
        for row in rows.iterator(chunk_size=BATCH):
            row.context = {k: v for k, v in row.context.items() if k != "title"}
            batch.append(row)
            if len(batch) >= BATCH:
                model.objects.bulk_update(batch, ["context"])
                batch = []
        if batch:
            model.objects.bulk_update(batch, ["context"])


# --- 3. kind config seeds -----------------------------------------------------


def _seed_rows(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    model = apps.get_model("notifications", "NotificationKindConfig")
    for kind, seed in SEEDS.items():
        model.objects.get_or_create(
            kind=kind,
            defaults={
                "channels": seed["channels"],
                "title": seed["title"],
                "title_ar": seed["title"],
                "title_en": seed["title"],
                "body": seed["body"],
                "body_ar": seed["body"],
                "body_en": seed["body"],
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0004_notifications_v2"),
    ]

    operations = [
        migrations.RunPython(_markers_to_deliveries, _deliveries_to_markers),
        migrations.RunPython(_add_title, _drop_title),
        migrations.RunPython(_seed_rows, migrations.RunPython.noop),
    ]
