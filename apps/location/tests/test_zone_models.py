import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError

from apps.location.models import Zone
from apps.location.tests.factories import CountryFactory
from apps.location.tests.factories import ZoneFactory

pytestmark = pytest.mark.django_db


def test_clean_normalises_code_and_region() -> None:
    zone = ZoneFactory.build(code=" SA-RD-1 ", region_code=" rd ")
    zone.clean()
    assert (zone.code, zone.region_code) == ("sa-rd-1", "RD")


def test_code_must_be_a_slug() -> None:
    zone = ZoneFactory.build(code="bad code!")
    with pytest.raises(ValidationError) as excinfo:
        zone.full_clean()
    assert "code" in excinfo.value.message_dict


def test_code_is_unique() -> None:
    ZoneFactory.create(code="sa-rd-1")
    with pytest.raises(IntegrityError):
        Zone.objects.create(
            country=CountryFactory.create(),
            code="sa-rd-1",
            region_code="RD",
            name_ar="x",
            name_en="x",
            geometry=ZoneFactory.build().geometry,
        )


def test_country_is_protected_by_its_zones() -> None:
    zone = ZoneFactory.create()
    with pytest.raises(ProtectedError):
        zone.country.delete()


def test_str_is_the_name() -> None:
    zone = ZoneFactory.build(name_ar="شمال", name_en="North")
    assert str(zone) in {"شمال", "North"}
