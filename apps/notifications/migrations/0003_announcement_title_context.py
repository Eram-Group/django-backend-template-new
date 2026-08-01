"""Backfill ``context["title"]`` on announcements authored before the composer.

ANNOUNCEMENT's catalog title stopped being a fixed gettext string and became
``"{title}"``, so its context_keys grew from {"message"} to {"title",
"message"}. Rows written under the old shape have no ``title`` key, and
``notification_render`` formats the title against the context - every one of
them would raise KeyError the next time it was rendered (the API renders inbox
rows on read, not just at send time, so this is not confined to old sends).

The backfill copy is the literal English word rather than a translated one:
this is data, and a gettext call here would freeze whichever locale happened
to be active while the migration ran.
"""

from django.db import migrations
from django.db.migrations.state import StateApps
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

LEGACY_TITLE = "Announcement"
BATCH = 500


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
    """Reverse only the rows this migration could have written.

    Scoped to the backfill copy so rolling back does not silently discard a
    title an operator actually typed.
    """
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


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_broadcast_channels_broadcast_joined_after_and_more"),
    ]

    operations = [migrations.RunPython(_add_title, _drop_title)]
