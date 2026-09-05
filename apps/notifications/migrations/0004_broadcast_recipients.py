# Broadcast.recipient_ids (a JSON list of user pks) -> Broadcast.recipients
# (a relation): the admin's autocomplete picker and exports need a real M2M.

from django.conf import settings
from django.db import migrations, models


def copy_recipient_ids(apps, schema_editor):
    Broadcast = apps.get_model("notifications", "Broadcast")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    for broadcast in Broadcast.objects.exclude(recipient_ids=[]).iterator():
        users = User.objects.filter(pk__in=broadcast.recipient_ids)
        broadcast.recipients.set(users)


def copy_recipients_back(apps, schema_editor):
    Broadcast = apps.get_model("notifications", "Broadcast")
    for broadcast in Broadcast.objects.iterator():
        broadcast.recipient_ids = [
            str(pk) for pk in broadcast.recipients.values_list("pk", flat=True)
        ]
        broadcast.save(update_fields=["recipient_ids"])


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0003_callable_choices_device_registration_id"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcast",
            name="recipients",
            field=models.ManyToManyField(
                blank=True,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="specific recipients",
            ),
        ),
        migrations.RunPython(copy_recipient_ids, copy_recipients_back),
        migrations.RemoveField(model_name="broadcast", name="recipient_ids"),
    ]
