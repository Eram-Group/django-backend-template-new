# The add-card-without-payment flow was removed: card_verification leaves
# the kind choices, and any rows that carry it (local test data only - the
# kind never shipped) are folded into "other" so full_clean stays valid.

from django.db import migrations, models


def _fold_card_verification(apps, schema_editor):
    payment_model = apps.get_model("payments", "Payment")
    payment_model.objects.filter(kind="card_verification").update(kind="other")


class Migration(migrations.Migration):

    dependencies = [
        ('payments', '0006_alter_payment_kind'),
    ]

    operations = [
        migrations.RunPython(_fold_card_verification, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='payment',
            name='kind',
            field=models.CharField(choices=[('wallet_topup', 'Wallet top-up'), ('other', 'Other')], max_length=20, verbose_name='kind'),
        ),
    ]
