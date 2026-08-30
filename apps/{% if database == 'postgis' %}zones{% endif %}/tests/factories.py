"""Zone factory - rows as the load sheet would create them, on a synthetic grid."""

from django.contrib.gis.geos import MultiPolygon
from django.contrib.gis.geos import Polygon
from factory.declarations import LazyAttribute
from factory.declarations import Sequence
from factory.declarations import SubFactory
from factory.django import DjangoModelFactory

from apps.common.tests import fake
from apps.location.tests.factories import CountryFactory
from apps.zones.models import Zone


def square_multipolygon(n: int) -> MultiPolygon:
    """A 0.04° square on a 100-column grid over the Arabian peninsula: valid,
    disjoint from every other n, deterministic, always inside WGS84."""
    x = 40.0 + 0.05 * (n % 100)
    y = 20.0 + 0.05 * (n // 100)
    ring = ((x, y), (x + 0.04, y), (x + 0.04, y + 0.04), (x, y + 0.04), (x, y))
    return MultiPolygon(Polygon(ring), srid=4326)


class ZoneFactory(DjangoModelFactory[Zone]):
    """Zones as the load sheet would create them from a well-formed file.

    Names come from fake.city per language (literal "ar"/"en": importing
    apps.users.constants from here would breach the app-independence
    contract). Codes walk a sequence so create_batch(n) yields n rows;
    django_get_or_create on code makes repeated calls for one code idempotent.
    """

    class Meta:
        model = Zone
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    country = SubFactory(CountryFactory)
    region_code = "TST"
    code = Sequence(lambda n: f"zz-tst-{n:04d}")
    name_ar = LazyAttribute(lambda o: fake.city("ar"))
    name_en = LazyAttribute(lambda o: fake.city("en"))
    geometry = Sequence(square_multipolygon)
    is_active = True
