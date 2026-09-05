# One NotificationKindConfig row per catalog kind, in its catalog starting
# state. The admin only edits rows (no add form: the kind set is the catalog's),
# so a kind ships its row here. A NEW kind = a new data migration calling the
# same seed (test_catalog pins that every kind has a row).
#
# The seed reads the CURRENT catalog on purpose: it is the code-side contract
# for the kind, and the row is only created when missing (never overwritten).

from django.db import migrations


def seed_rows(apps, schema_editor):
    from apps.notifications.catalog import CATALOG
    from apps.notifications.catalog import kind_config_seed

    NotificationKindConfig = apps.get_model("notifications", "NotificationKindConfig")
    for kind in CATALOG:
        NotificationKindConfig.objects.get_or_create(
            kind=str(kind), defaults=kind_config_seed(kind)
        )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0004_broadcast_recipients")]

    operations = [migrations.RunPython(seed_rows, migrations.RunPython.noop)]
