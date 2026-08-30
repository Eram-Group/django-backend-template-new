"""Location factories - Country from ISO data.

No fake values: a country's code, names, dial code and currency are real
reference data, so they come from apps.location.iso (not fake.py). Codes
walk the ISO list in order, so ``create_batch(n)`` yields n distinct rows,
and ``django_get_or_create`` on ``code`` makes repeated calls for one code
idempotent within a session.
"""

import base64

from django.core.files.base import ContentFile
from factory.declarations import LazyAttribute
from factory.declarations import Sequence
from factory.declarations import Trait
from factory.django import DjangoModelFactory

from apps.location.iso import CountryData
from apps.location.iso import iso_countries
from apps.location.models import Country

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
