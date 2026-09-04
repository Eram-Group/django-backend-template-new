import pytest
from django.core.exceptions import ValidationError

from apps.location.tests.factories import CountryFactory
from apps.zones import services
from apps.zones.exceptions import ZoneFileError
from apps.zones.exceptions import ZonesError
from apps.zones.models import Zone
from apps.zones.tests.factories import ZoneFactory
from apps.zones.tests.geo import SQUARE
from apps.zones.tests.geo import collection
from apps.zones.tests.geo import feature

pytestmark = pytest.mark.django_db


@pytest.fixture
def saudi():  # type: ignore[no-untyped-def]
    return CountryFactory.create(code="SA")


def test_load_creates_one_zone_per_feature(saudi) -> None:  # type: ignore[no-untyped-def]
    doc = collection(feature(zone_code=1), feature(zone_code=2, name_en="", name_ar=""))
    result = services.zones_load(country=saudi, document=doc)
    assert (result.created, result.updated, result.unnamed) == (2, 0, 1)
    named = Zone.objects.get(code="sa-rd-1")
    unnamed = Zone.objects.get(code="sa-rd-2")
    assert named.is_active
    assert named.country == saudi
    assert named.name_en == "Riyadh north"
    assert not unnamed.is_active
    assert unnamed.name_ar == unnamed.name_en == "sa-rd-2"


def test_reload_updates_geometry_and_names_but_keeps_is_active(saudi) -> None:  # type: ignore[no-untyped-def]
    services.zones_load(country=saudi, document=collection(feature()))
    zone = Zone.objects.get(code="sa-rd-1")
    services.zone_update(zone=zone, data={"is_active": False})
    moved = [[x + 1, y] for x, y in SQUARE]
    doc = collection(
        feature(
            name_en="Renamed",
            name_ar="أعيد تسميته",
            geometry={"type": "Polygon", "coordinates": [moved]},
        )
    )
    result = services.zones_load(country=saudi, document=doc)
    zone.refresh_from_db()
    assert (result.created, result.updated) == (0, 1)
    assert not zone.is_active
    assert (zone.name_en, zone.name_ar) == ("Renamed", "أعيد تسميته")
    assert zone.geometry.extent[0] == pytest.approx(47.6)


def test_reload_with_empty_names_keeps_operator_names(saudi) -> None:  # type: ignore[no-untyped-def]
    services.zones_load(country=saudi, document=collection(feature()))
    zone = Zone.objects.get(code="sa-rd-1")
    services.zone_update(zone=zone, data={"name_en": "Operator", "name_ar": "المشغل"})
    services.zones_load(
        country=saudi, document=collection(feature(name_en="", name_ar=""))
    )
    zone.refresh_from_db()
    assert (zone.name_en, zone.name_ar) == ("Operator", "المشغل")


def test_load_is_all_or_nothing(saudi) -> None:  # type: ignore[no-untyped-def]
    doc = collection(feature(zone_code=1), feature(zone_code=2, country_code="EG"))
    with pytest.raises(ZoneFileError):
        services.zones_load(country=saudi, document=doc)
    assert not Zone.objects.filter(code__startswith="sa-rd-").exists()


def test_update_allowlist_and_validation() -> None:
    zone = ZoneFactory.create()
    with pytest.raises(ZonesError, match="not updatable: code"):
        services.zone_update(zone=zone, data={"code": "other"})
    with pytest.raises(ValidationError):
        services.zone_update(zone=zone, data={"name_en": ""})
    zone.refresh_from_db()
    services.zone_update(zone=zone, data={"region_code": "new"})
    zone.refresh_from_db()
    assert zone.region_code == "NEW"
