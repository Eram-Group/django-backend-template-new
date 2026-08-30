"""Writes for countries - the load road and the flag re-fetch."""

from collections.abc import Iterable

from django.db import transaction

from apps.location import selectors
from apps.location.iso import iso_country
from apps.location.models import Country
from apps.location.tasks import fetch_country_flag


def countries_load(*, codes: Iterable[str]) -> list[Country]:
    """Create Country rows from ISO data for ``codes``.

    Unknown/incomplete codes and codes already loaded are skipped, not
    errors: the sheet only offers loadable ones, and a double submit must be
    harmless. Rows are committed first; each flag download is a task
    enqueued on commit, so a CDN hiccup can never undo or block the load.
    """
    loaded = selectors.country_loaded_codes()
    wanted = dict.fromkeys(code.upper() for code in codes)
    created: list[Country] = []
    with transaction.atomic():
        for code in wanted:
            if code in loaded:
                continue
            data = iso_country(code)
            if data is None:
                continue
            country = Country(
                code=data.code,
                alpha_3=data.alpha_3,
                name_ar=data.name_ar,
                name_en=data.name_en,
                dial_code=data.dial_code,
                phone_example=data.phone_example,
                max_phone_length=data.max_phone_length,
                currency=data.currency,
            )
            country.full_clean()
            country.save()
            created.append(country)
        transaction.on_commit(lambda: _enqueue_flags(created))
    return created


def country_flags_fetch(*, countries: Iterable[Country]) -> int:
    """(Re)download flags for ``countries`` - the admin retry path."""
    rows = list(countries)
    transaction.on_commit(lambda: _enqueue_flags(rows))
    return len(rows)


def _enqueue_flags(countries: list[Country]) -> None:
    for country in countries:
        fetch_country_flag.enqueue(str(country.pk))
