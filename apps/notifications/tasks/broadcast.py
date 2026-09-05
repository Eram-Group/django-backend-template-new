"""Dispatcher task - a trampoline into ``services.dispatch``.

Each run materializes ONE audience page and re-enqueues itself while more
remain (the service does both), so a 100k-user broadcast is a chain of
bounded tasks, not one long-running loop. Bulk queue, default priority:
transactional sends (priority 10) go first on the shared worker.
"""

from django.tasks import task

BULK_QUEUE = "bulk"


@task(queue_name=BULK_QUEUE)
def dispatch_broadcast(broadcast_id: str) -> None:
    # Recorded layering exception in pyproject.toml (function-level import).
    from apps.notifications.services.dispatch import broadcast_dispatch_page

    broadcast_dispatch_page(broadcast_id=broadcast_id)
