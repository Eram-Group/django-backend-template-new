from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0003_alter_user_created_at_alter_user_updated_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="language",
            field=models.CharField(
                choices=[("ar", "Arabic"), ("en", "English")],
                max_length=2,
                verbose_name="language",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="name",
            field=models.CharField(max_length=255, verbose_name="name"),
        ),
    ]
