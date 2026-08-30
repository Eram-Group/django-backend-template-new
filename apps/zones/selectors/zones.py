"""Reads for zones - every spatial question is one PostGIS query."""

from django.contrib.gis.geos import Point
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.zones.exceptions import InvalidCoordinatesError
from apps.zones.exceptions import ZoneNotFoundError
from apps.zones.models import Zone

MAX_LATITUDE = 90.0
MAX_LONGITUDE = 180.0


def zone_list_active() -> QuerySet[Zone]:
    return Zone.objects.filter(is_active=True).select_related("country")


def zone_get(*, code: str) -> Zone:
    try:
        return Zone.objects.select_related("country").get(code=code.strip().lower())
    except Zone.DoesNotExist as exc:
        raise ZoneNotFoundError(str(_("Zone not found."))) from exc


def zone_for_point(*, lat: float, lng: float) -> Zone | None:
    """The active zone containing the point - lowest ``code`` when several
    overlap (Meta.ordering), None outside every zone. ``contains`` excludes
    the boundary itself; a point exactly on an edge belongs to no zone."""
    if abs(lat) > MAX_LATITUDE or abs(lng) > MAX_LONGITUDE:
        raise InvalidCoordinatesError(str(_("Coordinates are outside WGS84 bounds.")))
    point = Point(lng, lat, srid=4326)
    return zone_list_active().filter(geometry__contains=point).first()


def zone_overlaps(*, zone: Zone) -> QuerySet[Zone]:
    """Zones sharing interior area with ``zone`` - neighbours that merely
    touch along a border are not overlaps."""
    return (
        Zone.objects.filter(geometry__intersects=zone.geometry)
        .exclude(pk=zone.pk)
        .exclude(geometry__touches=zone.geometry)
        .select_related("country")
    )
