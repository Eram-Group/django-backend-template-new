"""Template for scheduled jobs (EventBridge cron -> ECS run-task).

Thin wrapper: parse args, call one service/selector, report. Copy this
shape for every future scheduled command.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.users.selectors import user_list


class Command(BaseCommand):
    help = "Sample scheduled job - reports user counts (replace with real work)."

    def handle(self, *args: Any, **options: Any) -> None:
        total = user_list().count()
        active = user_list(is_active=True).count()
        message = f"sample_scheduled_job OK: {active}/{total} active users"
        self.stdout.write(self.style.SUCCESS(message))
