"""Delivery task - a trampoline into ``services.execution``.

The body (claim, send per channel, record outcomes) is business logic and
lives in services; the task only names the queue entry point. Priority 10:
a single transactional send (an OTP, a payment receipt) outranks broadcast
batches (priority 0) on the shared worker - queue names label work, only
priority orders it.
"""

from django.tasks import task


@task(priority=10)
def deliver_notifications(delivery_ids: list[str]) -> None:
    # Recorded layering exception in pyproject.toml (function-level import).
    from apps.notifications.services.execution import execute_deliveries

    execute_deliveries(delivery_ids=delivery_ids)
