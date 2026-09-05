"""Refund executor: runs the non-idempotent provider call in the worker.

``payment_refund_start`` committed REFUND_PENDING + the wallet debit with
the admin request; this task performs the provider call and finalization
in the worker, outside any open transaction, so each
phase commits independently and a crash leaves a reconcilable
REFUND_PENDING row instead of silently rolling back to PAID.

A provider failure escapes and fails the task loudly (FAILED row + Sentry);
the write-ahead marker in the service makes manual re-enqueue safe.
"""

import uuid

from django.tasks import task


@task(priority=10)  # transactional: outranks broadcast batches
def process_payment_refund(*, payment_id: str, actor_id: str) -> None:
    # Trampoline into the owning service: refund finalization moves money
    # (wallet_apply), which is business logic and stays in services -
    # recorded layering exception in pyproject.toml.
    from apps.payments.services.refunds import payment_refund_execute
    from apps.users.models import User

    # A staff row deleted between enqueue and run must not strand the refund
    # in REFUND_PENDING: the executor accepts an unknown actor.
    actor = User.objects.filter(pk=actor_id).first()
    payment_refund_execute(payment_id=uuid.UUID(payment_id), actor=actor)
