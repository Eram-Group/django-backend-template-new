"""countries_load + country_flags_fetch (flag download stubbed at the transport)."""

from typing import Any

import httpx
import pytest
import respx

from apps.location import services
from apps.location.clients.flags import FLAG_URL
from apps.location.models import Country
from apps.location.tests.factories import TINY_PNG
from apps.location.tests.factories import CountryFactory

pytestmark = pytest.mark.django_db


def _flag_route(code: str) -> respx.Route:
    return respx.get(FLAG_URL.format(code=code.lower()))


@respx.mock
def test_load_creates_rows_from_iso_data_and_fetches_flags(
    run_enqueued_tasks: Any,
) -> None:
    Country.objects.filter(code__in=["EG", "AE"]).delete()
    route = _flag_route("EG").mock(return_value=httpx.Response(200, content=TINY_PNG))
    _flag_route("AE").mock(return_value=httpx.Response(200, content=TINY_PNG))

    with run_enqueued_tasks():
        created = services.countries_load(codes=["eg", "AE", "EG"])  # dup + case

    assert sorted(c.code for c in created) == ["AE", "EG"]
    egypt = Country.objects.get(code="EG")
    assert egypt.currency == "EGP"
    assert egypt.name_ar == "مصر"
    assert egypt.name_en == "Egypt"
    assert egypt.dial_code == "+20"
    assert egypt.is_active
    assert route.called
    assert egypt.flag.name == "location/flags/eg.png"


@respx.mock
def test_load_skips_unknown_incomplete_and_loaded_codes(
    run_enqueued_tasks: Any,
) -> None:
    CountryFactory.create(code="SA")
    before = Country.objects.count()

    with run_enqueued_tasks():
        created = services.countries_load(codes=["SA", "ZZ", "AQ"])

    assert created == []
    assert Country.objects.count() == before


@respx.mock
def test_load_survives_a_failed_flag_download(
    run_enqueued_tasks: Any,
) -> None:
    Country.objects.filter(code="JO").delete()
    _flag_route("JO").mock(side_effect=httpx.ConnectError("down"))

    with run_enqueued_tasks():
        (country,) = services.countries_load(codes=["JO"])

    country.refresh_from_db()
    assert country.currency == "JOD"
    assert country.flag == ""  # the task failed; the row is here regardless


@respx.mock
def test_flags_fetch_enqueues_one_task_per_country(
    run_enqueued_tasks: Any,
) -> None:
    countries = [CountryFactory.create(code="KW"), CountryFactory.create(code="QA")]
    kw = _flag_route("KW").mock(return_value=httpx.Response(200, content=TINY_PNG))
    qa = _flag_route("QA").mock(return_value=httpx.Response(200, content=TINY_PNG))

    with run_enqueued_tasks():
        count = services.country_flags_fetch(
            countries=Country.objects.filter(pk__in=[c.pk for c in countries])
        )

    assert count == 2
    assert kw.called
    assert qa.called
