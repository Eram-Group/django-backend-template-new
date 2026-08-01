"""One explicit config row per notification kind; the override layer retires.

Channel policy used to be catalog defaults + sparse NotificationChannelOverride
pins; message copy used to be gettext in code. Both move onto a single
NotificationKindConfig row per kind (channels list + ar/en title/body columns),
seeded here from the catalog values that were live at this migration's birth.

The seeds are frozen literals, not catalog imports: the catalog keeps evolving
while this file must replay identically forever. English text goes into BOTH
language columns (every msgstr in the repo's .po files is empty, so English is
what both locales effectively rendered before this change); operators localize
in the admin. Historical models have no modeltranslation descriptors, so each
field writes all three columns (base + _ar + _en) explicitly.

Override rows are dropped without translation: their sparse pins have no
faithful mapping onto explicit lists (a pin only overrode one channel), and
the only live deployment target is local/dev seed data.
"""

import django.db.models.functions.datetime
from django.db import migrations
from django.db import models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps

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


def _seed_rows(apps: StateApps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    model = apps.get_model("notifications", "NotificationKindConfig")
    for kind, seed in SEEDS.items():
        model.objects.create(
            kind=kind,
            channels=seed["channels"],
            title=seed["title"],
            title_ar=seed["title"],
            title_en=seed["title"],
            body=seed["body"],
            body_ar=seed["body"],
            body_en=seed["body"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_announcement_title_context"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificationKindConfig",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_default=models.Func(function="uuidv7"),
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_default=django.db.models.functions.datetime.Now(),
                        db_index=True,
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        db_default=django.db.models.functions.datetime.Now(),
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("welcome", "Welcome"),
                            ("announcement", "Announcement"),
                            ("payment_paid", "Payment received"),
                            ("wallet_credited", "Wallet credited"),
                        ],
                        max_length=50,
                        unique=True,
                        verbose_name="action",
                    ),
                ),
                (
                    "channels",
                    models.JSONField(blank=True, default=list, verbose_name="channels"),
                ),
                ("title", models.CharField(max_length=255, verbose_name="title")),
                (
                    "title_ar",
                    models.CharField(max_length=255, null=True, verbose_name="title"),
                ),
                (
                    "title_en",
                    models.CharField(max_length=255, null=True, verbose_name="title"),
                ),
                ("body", models.TextField(verbose_name="body")),
                ("body_ar", models.TextField(null=True, verbose_name="body")),
                ("body_en", models.TextField(null=True, verbose_name="body")),
            ],
            options={
                "verbose_name": "notification action",
                "verbose_name_plural": "notification actions",
                "ordering": ["kind"],
            },
        ),
        migrations.RunPython(_seed_rows, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="notificationchanneloverride",
            name="uniq_override_kind_channel",
        ),
        migrations.DeleteModel(
            name="NotificationChannelOverride",
        ),
    ]
