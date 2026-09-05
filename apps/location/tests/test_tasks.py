"""fetch_country_flag - idempotent, overwriting, loud on failure."""

import uuid
from typing import Any

import httpx
import pytest
import respx
from django.tasks import TaskResultStatus

from apps.location.clients.flags import FLAG_URL
from apps.location.models import Country
from apps.location.tasks import fetch_country_flag
from apps.location.tests.factories import TINY_PNG
from apps.location.tests.factories import CountryFactory

pytestmark = pytest.mark.django_db


@respx.mock
def test_refetch_overwrites_the_same_file(run_enqueued_tasks: Any) -> None:
    country = CountryFactory.create(code="EG")
    respx.get(FLAG_URL.format(code="eg")).mock(
        return_value=httpx.Response(200, content=TINY_PNG)
    )

    with run_enqueued_tasks():
        fetch_country_flag.enqueue(str(country.pk))
        fetch_country_flag.enqueue(str(country.pk))

    country.refresh_from_db()
    assert country.flag.name == "location/flags/eg.png"
    storage = Country._meta.get_field("flag").storage
    _dirs, files = storage.listdir("location/flags")
    assert files == ["eg.png"]  # no eg_XXXX.png pile-up


@respx.mock
def test_failure_marks_the_task_failed_and_leaves_no_flag(
    run_enqueued_tasks: Any,
) -> None:
    country = CountryFactory.create(code="EG")
    respx.get(FLAG_URL.format(code="eg")).mock(return_value=httpx.Response(503))

    with run_enqueued_tasks() as records:
        fetch_country_flag.enqueue(str(country.pk))

    assert [r.status for r in records] == [TaskResultStatus.FAILED]
    country.refresh_from_db()
    assert country.flag == ""


def test_missing_row_is_a_noop(run_enqueued_tasks: Any) -> None:
    with run_enqueued_tasks() as records:
        fetch_country_flag.enqueue(str(uuid.uuid4()))

    assert [r.status for r in records] == [TaskResultStatus.SUCCESSFUL]
