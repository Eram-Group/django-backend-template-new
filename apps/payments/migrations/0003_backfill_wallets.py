"""Wallets are provisioned at signup (user_post_signup) as of this change;
backfill one DEFAULT_CURRENCY wallet for every pre-existing user so payment
flows can rely on plain wallet_get."""

from django.db import migrations

BATCH = 1000
DEFAULT_CURRENCY = "SAR"  # frozen copy - migrations never import app code


def create_missing_wallets(apps, schema_editor):
    user_model = apps.get_model("users", "User")
    wallet_model = apps.get_model("payments", "Wallet")
    missing = user_model.objects.filter(wallet__isnull=True).values_list(
        "pk", flat=True
    )
    wallet_model.objects.bulk_create(
        (
            wallet_model(user_id=pk, currency=DEFAULT_CURRENCY)
            for pk in missing.iterator()
        ),
        batch_size=BATCH,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0002_alter_payment_created_at_and_more"),
        ("users", "0003_alter_user_created_at_alter_user_updated_at"),
    ]

    operations = [
        migrations.RunPython(create_missing_wallets, migrations.RunPython.noop),
    ]
