"""Reads for countries."""

from django.db.models import QuerySet

from apps.location.models import Country


def country_list_active() -> QuerySet[Country]:
    """The public list - ordered by name in the active language (Meta)."""
    return Country.objects.filter(is_active=True)


def country_loaded_codes() -> set[str]:
    """Codes already present, active or not - what the load sheet greys out."""
    return set(Country.objects.values_list("code", flat=True))
