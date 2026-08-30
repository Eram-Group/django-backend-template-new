"""Location factories - Country from ISO data, Zone from a synthetic grid.

No fake values: a country's code, names, dial code and currency are real
reference data, so they come from apps.location.iso (not fake.py). Codes
walk the ISO list in order, so ``create_batch(n)`` yields n distinct rows
and a re-run on a --reuse-db database re-hits the same ones
(django_get_or_create on ``code``).
"""

import base64

from django.contrib.gis.geos import MultiPolygon
from django.contrib.gis.geos import Polygon
from django.core.files.base import ContentFile
from factory.declarations import LazyAttribute
from factory.declarations import Sequence
from factory.declarations import SubFactory
from factory.declarations import Trait
from factory.django import DjangoModelFactory

from apps.common.tests import fake
from apps.location.iso import CountryData
from apps.location.iso import iso_countries
from apps.location.models import Country
from apps.location.models import Zone

_CODES = [country.code for country in iso_countries()]

# A 1x1 transparent PNG - the session-shared tiny image for flag tests.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _iso(code: str) -> CountryData:
    return next(c for c in iso_countries() if c.code == code.upper())


class CountryFactory(DjangoModelFactory[Country]):
    class Meta:
        model = Country
        django_get_or_create = ["code"]
        skip_postgeneration_save = True

    class Params:
        with_flag = Trait(
            flag=LazyAttribute(
                lambda o: ContentFile(TINY_PNG, name=f"{o.code.lower()}.png")
            )
        )

    code = Sequence(lambda n: _CODES[n % len(_CODES)])
    alpha_3 = LazyAttribute(lambda o: _iso(o.code).alpha_3)
    name_ar = LazyAttribute(lambda o: _iso(o.code).name_ar)
    name_en = LazyAttribute(lambda o: _iso(o.code).name_en)
    dial_code = LazyAttribute(lambda o: _iso(o.code).dial_code)
    phone_example = LazyAttribute(lambda o: _iso(o.code).phone_example)
    max_phone_length = LazyAttribute(lambda o: _iso(o.code).max_phone_length)
    currency = LazyAttribute(lambda o: _iso(o.code).currency)
    is_active = True


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
    contract). Codes walk a sequence so create_batch(n) yields n rows and a
    --reuse-db re-run hits the same ones (django_get_or_create on code).
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
