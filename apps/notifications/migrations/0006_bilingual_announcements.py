# Announcements are authored in both languages (title_ar/title_en,
# message_ar/message_en). The ANNOUNCEMENT config row becomes one passthrough
# per language, and every existing announcement context (broadcast + inbox
# rows) gets the four keys, the old single value copied to both languages, so
# nothing already sent stops rendering.

from django.db import migrations

KIND = "announcement"
OLD_TO_NEW = {"title": ("title_ar", "title_en"), "message": ("message_ar", "message_en")}


def _widen(context):
    if not isinstance(context, dict) or "title" not in context or "message" not in context:
        return None
    widened = {}
    for old, new_keys in OLD_TO_NEW.items():
        for new in new_keys:
            widened[new] = context[old]
    return widened


def forwards(apps, schema_editor):
    NotificationKindConfig = apps.get_model("notifications", "NotificationKindConfig")
    NotificationKindConfig.objects.filter(kind=KIND).update(
        title="{title_en}",
        body="{message_en}",
        title_ar="{title_ar}",
        title_en="{title_en}",
        body_ar="{message_ar}",
        body_en="{message_en}",
    )
    for model_name in ("Broadcast", "Notification"):
        model = apps.get_model("notifications", model_name)
        for row in model.objects.filter(kind=KIND).iterator():
            widened = _widen(row.context)
            if widened is not None:
                row.context = widened
                row.save(update_fields=["context"])


def backwards(apps, schema_editor):
    NotificationKindConfig = apps.get_model("notifications", "NotificationKindConfig")
    NotificationKindConfig.objects.filter(kind=KIND).update(
        title="{title}",
        body="{message}",
        title_ar="{title}",
        title_en="{title}",
        body_ar="{message}",
        body_en="{message}",
    )
    for model_name in ("Broadcast", "Notification"):
        model = apps.get_model("notifications", model_name)
        for row in model.objects.filter(kind=KIND).iterator():
            context = row.context if isinstance(row.context, dict) else {}
            if "title_en" in context:
                row.context = {
                    "title": context["title_en"],
                    "message": context.get("message_en", ""),
                }
                row.save(update_fields=["context"])


class Migration(migrations.Migration):
    dependencies = [("notifications", "0005_seed_kind_configs")]

    operations = [migrations.RunPython(forwards, backwards)]
