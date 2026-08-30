"""Reads for countries."""

from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _

from apps.location.exceptions import CountryNotFoundError
from apps.location.models import Country


def country_list_active() -> QuerySet[Country]:
    """The public list - ordered by name in the active language (Meta)."""
    return Country.objects.filter(is_active=True)


def country_loaded_codes() -> set[str]:
    """Codes already present, active or not - what the load sheet greys out."""
    return set(Country.objects.values_list("code", flat=True))


def country_get_by_code(*, code: str) -> Country:
    try:
        return Country.objects.get(code=code.upper())
    except Country.DoesNotExist as exc:
        raise CountryNotFoundError(str(_("Country not found."))) from exc
