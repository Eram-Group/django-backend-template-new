import pytest
from django.contrib.gis.geos import MultiPolygon
from django.contrib.gis.geos import Polygon

from apps.zones import selectors
from apps.zones.exceptions import InvalidCoordinatesError
from apps.zones.tests.factories import ZoneFactory
from apps.zones.tests.geo import HOLE
from apps.zones.tests.geo import SQUARE

pytestmark = pytest.mark.django_db


def _multi(*rings: list[list[float]]) -> MultiPolygon:
    return MultiPolygon(Polygon(*rings), srid=4326)


def test_point_lookup_inside_outside_and_hole() -> None:
    ZoneFactory.create(code="sa-a-1", geometry=_multi(SQUARE, HOLE))
    assert selectors.zone_for_point(lat=24.65, lng=46.65) is not None
    assert selectors.zone_for_point(lat=24.70, lng=46.70) is None  # in the hole
    assert selectors.zone_for_point(lat=25.5, lng=46.7) is None


def test_lowest_code_wins_on_overlap_and_inactive_is_skipped() -> None:
    ZoneFactory.create(code="sa-b-2", geometry=_multi(SQUARE))
    ZoneFactory.create(code="sa-a-1", geometry=_multi(SQUARE), is_active=False)
    ZoneFactory.create(code="sa-a-9", geometry=_multi(SQUARE))
    zone = selectors.zone_for_point(lat=24.65, lng=46.65)
    assert zone is not None
    assert zone.code == "sa-a-9"


@pytest.mark.parametrize(("lat", "lng"), [(91, 0), (-91, 0), (0, 181), (0, -181)])
def test_out_of_range_coordinates_are_rejected(lat: float, lng: float) -> None:
    with pytest.raises(InvalidCoordinatesError):
        selectors.zone_for_point(lat=lat, lng=lng)


def test_overlaps_ignore_neighbours_that_only_touch() -> None:
    zone = ZoneFactory.create(code="sa-a-1", geometry=_multi(SQUARE))
    shifted = [[x + 0.1, y] for x, y in SQUARE]
    neighbour = [[x + 0.2, y] for x, y in SQUARE]  # shares the east edge only
    ZoneFactory.create(code="sa-a-2", geometry=_multi(shifted))
    ZoneFactory.create(code="sa-a-3", geometry=_multi(neighbour))
    assert [z.code for z in selectors.zone_overlaps(zone=zone)] == ["sa-a-2"]


def test_active_list_skips_inactive_zones() -> None:
    ZoneFactory.create(code="sa-a-1", is_active=False)
    active = ZoneFactory.create(code="sa-a-2")
    assert list(selectors.zone_list_active().filter(code__startswith="sa-a-")) == [
        active
    ]
