import pytest
from django.db import IntegrityError

from apps.location.models import Country
from apps.location.tests.factories import CountryFactory

pytestmark = pytest.mark.django_db


def test_clean_uppercases_codes() -> None:
    country = CountryFactory.build(code="sa", alpha_3="sau", currency="sar")
    country.clean()
    assert (country.code, country.alpha_3, country.currency) == ("SA", "SAU", "SAR")


def test_code_is_unique() -> None:
    CountryFactory.create(code="SA")
    with pytest.raises(IntegrityError):
        Country.objects.create(
            code="SA",
            alpha_3="XXX",
            name_ar="x",
            name_en="x",
            dial_code="+1",
            phone_example="+1",
            max_phone_length=1,
            currency="USD",
        )


def test_str_is_the_name() -> None:
    country = CountryFactory.build(code="EG")
    assert str(country) == country.name
