"""Country endpoints."""

from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import Router

from apps.location import selectors
from apps.location.models import Country
from apps.location.schemas import CountrySummary

router = Router(tags=["location"])


# Public (auth=None): the signup/phone screen needs the list before login.
# Unpaginated on purpose: the set is <= 250 rows and CursorPagination orders
# by -pk, which would break the alphabetical order clients rely on.
@router.get(
    "/countries",
    response=list[CountrySummary],
    auth=None,
    summary="Active countries",
)
def country_list(request: HttpRequest) -> QuerySet[Country]:
    return selectors.country_list_active()
