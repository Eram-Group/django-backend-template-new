"""Recovery sweep for transactional sends (no auto-retry exists).

Resets stale PROCESSING rows (worker died mid-batch) and re-enqueues every
PENDING orphan; ``--include-failed`` also retries FAILED rows. Broadcasts
are resumed from their admin page ("Resume incomplete"), not from here.
Idempotent - the executor's claim makes over-enqueued rows no-ops.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.core.management.base import CommandParser

from apps.notifications import services


class Command(BaseCommand):
    help = "Re-enqueue incomplete transactional notification deliveries."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--include-failed",
            action="store_true",
            help="Also reset FAILED rows to PENDING before re-enqueueing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        summary = services.deliveries_resume(
            broadcast=None, include_failed=options["include_failed"]
        )
        for key, value in summary.items():
            self.stdout.write(f"{key}: {value}")
