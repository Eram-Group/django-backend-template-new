"""The release step as ONE command: ``manage.py release``.

Runs before every rollout - the CDK release trigger and the CD workflow both
execute it on a one-off worker task - and in the CI image smoke job: deploy
checks (any warning fails), migrations, the cache table, static files.
``--skip-static`` exists for the image smoke job alone: its environment has no
object store to collect into.
"""

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandParser


class Command(BaseCommand):
    help = "Deploy checks, migrate, createcachetable, collectstatic."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--skip-static",
            action="store_true",
            help="No collectstatic (the CI image smoke job has no object store).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        call_command("check", deploy=True, fail_level="WARNING")
        call_command("migrate", interactive=False)
        call_command("createcachetable")
        if not options["skip_static"]:
            call_command("collectstatic", interactive=False)
