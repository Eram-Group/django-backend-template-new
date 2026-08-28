"""Drop the v1 per-channel markers - 0005 already copied them into deliveries."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0005_notifications_v2_data"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="notification",
            name="push_sent_at",
        ),
        migrations.RemoveField(
            model_name="notification",
            name="sms_sent_at",
        ),
    ]
