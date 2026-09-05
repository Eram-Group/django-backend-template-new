"""Project-wide pytest fixtures."""

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import AbstractContextManager
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import stamina
from django.core.cache import cache
from django.test import Client
from django_tasks_db.management.commands.db_worker import Worker
from django_tasks_db.models import DBTaskResult
from pytest_django import Settings

from apps.notifications.tests.factories import seed_kind_configs
from apps.users.models import User
from apps.users.tests.factories import UserFactory

#: A test that queues more tasks than this is looping, not fanning out.
_DRAIN_LIMIT = 1000


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup: None, django_db_blocker: Any) -> None:
    """Every kind's NotificationKindConfig row exists from the start, as if
    an operator had saved each card once."""
    with django_db_blocker.unblock():
        seed_kind_configs()


@pytest.fixture(autouse=True, scope="session")
def _stamina_testing() -> Iterator[None]:
    """Outbound-HTTP retries never sleep (or retry) in the suite.

    Tests that exercise retry behaviour re-enable attempts locally with
    ``stamina.set_testing(True, attempts=N)`` - still without sleeping.
    """
    stamina.set_testing(True, attempts=1)
    yield
    stamina.set_testing(False)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Rate-limit counters (allauth, ninja throttles) live in the cache, and
    the test cache is in-process: without this a burst in one test would
    throttle the next."""
    cache.clear()


@pytest.fixture(autouse=True)
def _tmp_media_root(settings: Settings, tmp_path: Path) -> None:
    """Tests never write into the real media/ directory; per test, because
    the flag-download tests assert on the directory's exact contents."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def run_enqueued_tasks(
    db: None, django_capture_on_commit_callbacks: Any
) -> Callable[[], AbstractContextManager[list[Any]]]:
    """Run everything the block deferred: its ``on_commit`` callbacks (the
    external side effects services still defer), then the database task
    queue - the production worker's own runner over the rows the block
    enqueued (task rows sit in the test transaction like any other row).
    Tasks a task enqueues run too; the yielded list collects every task
    run record, in run order, so a test can assert how many tasks ran and
    how they ended (``status``).
    """
    worker = Worker(
        queue_names=["*"],
        interval=0,
        batch=True,
        backend_name="default",
        startup_delay=False,
        max_tasks=None,
        worker_id="pytest",
    )

    @contextmanager
    def drain() -> Iterator[list[Any]]:
        records: list[Any] = []
        with django_capture_on_commit_callbacks(execute=True):
            yield records
        for _ in range(_DRAIN_LIMIT):
            row = DBTaskResult.objects.ready().filter(backend_name="default").first()
            if row is None:
                return
            row.claim("pytest")
            worker.run_task(row)
            row.refresh_from_db()
            records.append(row)
        pytest.fail(f"the task queue did not drain within {_DRAIN_LIMIT} tasks")

    return drain


@pytest.fixture
def user(db: None) -> User:
    """A regular, passwordless user with a VERIFIED email.

    UserFactory owns that invariant (verified primary EmailAddress via
    post_generation, unusable password) - single source of truth.
    """
    return UserFactory.create()


@pytest.fixture
def auth_client(user: User) -> Client:
    """A test client logged in as ``user`` (session cookie, the browser road)."""
    client = Client()
    client.force_login(user)
    return client
