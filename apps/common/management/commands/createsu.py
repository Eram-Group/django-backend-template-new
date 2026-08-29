"""Idempotent superuser bootstrap from DJANGO_SUPERUSER_* env fields.

Runs anywhere the image runs - locally via `just superuser`, in AWS as the
one-off ECS task that creates the first production superuser (plain
`createsuperuser --noinput` errors on re-run; this exits cleanly).
"""

from typing import Any

from django.core.management.base import BaseCommand

from config.env import env


class Command(BaseCommand):
    help = "Create the DJANGO_SUPERUSER_* superuser if it does not exist."

    def handle(self, *args: Any, **options: Any) -> None:
        from apps.users.constants import Language
        from apps.users.models import User

        email = env.DJANGO_SUPERUSER_EMAIL
        if User.objects.filter(email=email, is_superuser=True).exists():
            self.stdout.write("Superuser already exists - nothing to do.")
            return
        # The bootstrap account of an Arabic-first product; both fields are
        # editable in the admin afterwards.
        User.objects.create_superuser(
            email=email,
            password=env.DJANGO_SUPERUSER_PASSWORD.get_secret_value(),
            name="Superuser",
            language=Language.ARABIC,
        )
        self.stdout.write(self.style.SUCCESS(f"Superuser {email} created."))
