"""ISO country data read from pycountry, babel (CLDR) and phonenumbers.

The ONLY source of a Country row's reference values: an operator never types
a dial code or an Arabic name - the load sheet offers exactly the set this
module can describe completely, and ``services.countries_load`` copies the
values into columns (so search/filter/order/export work on plain fields).
"""

from dataclasses import dataclass
from functools import lru_cache

import phonenumbers
import pycountry
from babel import Locale
from babel.numbers import get_territory_currencies

_ARABIC = Locale.parse("ar")
_ENGLISH = Locale.parse("en")
_REGIONAL_INDICATOR_A = 0x1F1E6


@dataclass(frozen=True, slots=True)
class CountryData:
    """Everything stored on a Country, sourced from the ISO libraries."""

    code: str
    alpha_3: str
    name_en: str
    name_ar: str
    dial_code: str
    phone_example: str
    max_phone_length: int
    currency: str

    @property
    def flag_emoji(self) -> str:
        """Regional-indicator pair, so the load sheet shows a flag before an
        image is stored."""
        return "".join(
            chr(_REGIONAL_INDICATOR_A + ord(char) - ord("A")) for char in self.code
        )


def iso_country(code: str) -> CountryData | None:
    """Data for an alpha-2 code, or None when any source is incomplete.

    Antarctica (AQ) is the canonical gap: an ISO code with no tender currency
    and no dial plan. Half-populated rows are never offered.
    """
    code = code.upper()
    country = pycountry.countries.get(alpha_2=code)
    if country is None:
        return None
    currencies = get_territory_currencies(code, tender=True, non_tender=False)
    example = phonenumbers.example_number(code)
    if not currencies or example is None:
        return None
    name_en = _ENGLISH.territories.get(code)
    name_ar = _ARABIC.territories.get(code)
    if not name_en or not name_ar:
        return None
    return CountryData(
        code=code,
        alpha_3=country.alpha_3,
        name_en=name_en,
        name_ar=name_ar,
        dial_code=f"+{phonenumbers.country_code_for_region(code)}",
        phone_example=phonenumbers.format_number(
            example, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        ),
        # One example's national digit count: a client-side input hint, not
        # a validator - phonenumbers validates real numbers.
        max_phone_length=len(str(example.national_number)),
        currency=currencies[0],
    )


@lru_cache(maxsize=1)
def iso_countries() -> tuple[CountryData, ...]:
    """Every country with complete data, sorted by English name."""
    complete = (iso_country(country.alpha_2) for country in pycountry.countries)
    return tuple(sorted((c for c in complete if c), key=lambda c: c.name_en))
