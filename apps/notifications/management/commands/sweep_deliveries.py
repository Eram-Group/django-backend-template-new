"""Manual recovery: re-enqueue incomplete deliveries (no auto-retry exists).

With --broadcast: resume that broadcast (dead dispatcher, stale PROCESSING,
optional FAILED). Without: sweep transactional orphans. Both paths are
idempotent - the executor's claim makes over-enqueued rows no-ops.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandParser

from apps.notifications import services
from apps.notifications.models import Broadcast


class Command(BaseCommand):
    help = "Re-enqueue incomplete notification deliveries (manual recovery)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--broadcast",
            default=None,
            help="Broadcast id to resume; omit to sweep transactional sends.",
        )
        parser.add_argument(
            "--include-failed",
            action="store_true",
            help="Also reset FAILED rows to PENDING before re-enqueueing.",
        )
        parser.add_argument(
            "--stale-minutes",
            type=int,
            default=30,
            help="PROCESSING rows idle this long count as crashed (default 30).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["broadcast"]:
            broadcast = Broadcast.objects.get(pk=options["broadcast"])
            summary = services.broadcast_resume(
                broadcast=broadcast,
                include_failed=options["include_failed"],
                stale_minutes=options["stale_minutes"],
            )
        else:
            summary = services.deliveries_sweep_transactional(
                include_failed=options["include_failed"],
                stale_minutes=options["stale_minutes"],
            )
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")
