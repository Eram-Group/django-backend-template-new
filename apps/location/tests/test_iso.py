"""The ISO source - pure, no database."""

from apps.location.iso import iso_countries
from apps.location.iso import iso_country

INCOMPLETE_CODE = "AQ"  # Antarctica: ISO code, no tender currency, no dial plan


def test_build_returns_full_data() -> None:
    country = iso_country("SA")
    assert country is not None
    assert country.name_en == "Saudi Arabia"
    assert country.name_ar == "المملكة العربية السعودية"
    assert country.alpha_3 == "SAU"
    assert country.currency == "SAR"
    assert country.dial_code == "+966"
    assert country.max_phone_length == 10  # 9 or 10 digits: the max, not an example
    assert country.phone_example.startswith("+966 ")
    assert country.flag_emoji == "🇸🇦"


def test_max_phone_length_is_the_dial_plans_longest_number() -> None:
    """A client may use it as ``maxlength``: an example's length (EG 9,
    DE 8) would truncate valid numbers."""
    assert iso_country("EG").max_phone_length == 10  # type: ignore[union-attr]
    assert iso_country("DE").max_phone_length == 15  # type: ignore[union-attr]
    assert iso_country("US").max_phone_length == 10  # type: ignore[union-attr]


def test_code_is_normalised() -> None:
    assert iso_country("eg") == iso_country("EG")


def test_incomplete_or_unknown_codes_are_none() -> None:
    assert iso_country(INCOMPLETE_CODE) is None
    assert iso_country("ZZ") is None


def test_loadable_set_is_complete_and_sorted() -> None:
    countries = iso_countries()
    codes = {country.code for country in countries}
    assert {"SA", "EG", "AE"} <= codes
    assert INCOMPLETE_CODE not in codes
    assert [c.name_en for c in countries] == sorted(c.name_en for c in countries)
    assert iso_countries() is countries  # cached
