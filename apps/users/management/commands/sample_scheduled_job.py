"""Template for scheduled jobs (EventBridge cron -> ECS run-task).

Thin wrapper: parse args, call one service/selector, report. Copy this
shape for every future scheduled command.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.users.selectors import get_user_list
from apps.users.selectors.users import get_user_count


class Command(BaseCommand):
    help = "Sample scheduled job - reports user counts (replace with real work)."

    def handle(self, *args: Any, **options: Any) -> None:
        total = get_user_count()
        active = get_user_count(is_active=True)
        message = f"sample_scheduled_job OK: {active}/{total} active users"
        self.stdout.write(self.style.SUCCESS(message))
