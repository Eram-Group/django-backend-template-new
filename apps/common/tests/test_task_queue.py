"""The queue is the database: task rows share the service's transaction."""

from typing import Any

import pytest
from django.db import transaction
from django_tasks_db.models import DBTaskResult

from apps.users import services
from apps.users.constants import Language
from apps.users.models import User


class _AbortError(Exception):
    pass


def _signup_then_abort(email: str) -> None:
    with transaction.atomic():
        services.user_create(
            user=User(), email=email, name="Rolled Back", language=Language.ENGLISH
        )
        assert DBTaskResult.objects.count() >= 1  # queued, not yet committed
        raise _AbortError


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_write_leaves_no_task_behind() -> None:
    """A signup that rolls back must not send a welcome email: the task row
    is inside the same transaction as the user row."""
    with pytest.raises(_AbortError):
        _signup_then_abort("rollback@example.com")

    assert not User.objects.filter(email="rollback@example.com").exists()
    assert DBTaskResult.objects.count() == 0


@pytest.mark.django_db(transaction=True)
def test_a_committed_write_always_has_its_task(run_enqueued_tasks: Any) -> None:
    with run_enqueued_tasks() as records:
        services.user_create(
            user=User(),
            email="committed@example.com",
            name="Committed User",
            language=Language.ENGLISH,
        )

    assert records, "the signup queued nothing"
    assert {r.status for r in records} == {"SUCCESSFUL"}
