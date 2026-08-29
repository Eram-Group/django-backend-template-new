"""Changelist helpers for enum-valued columns that carry no ``choices``.

Fields validated by a validator (not ``choices=``) show raw values in the
admin; these give back the human labels for list columns and sidebar
filters from the TextChoices enum itself.
"""

from collections.abc import Callable
from typing import Any

from django.contrib import admin
from django.db import models
from django.db.models import QuerySet
from django.http import HttpRequest
from django_stubs_ext import StrOrPromise


def enum_column(
    field: str, enum: type[models.TextChoices], *, description: StrOrPromise
) -> Callable[[Any], str]:
    """A ``list_display`` entry showing ``enum``'s label for ``obj.<field>``
    (raw value when it is not a member - a retired kind stays readable)."""
    labels = {str(value): str(label) for value, label in enum.choices}

    @admin.display(description=description, ordering=field)
    def column(obj: Any) -> str:
        value = str(getattr(obj, field))
        return labels.get(value, value)

    column.__name__ = f"{field}_label"
    return column


def enum_filter(
    field: str, enum: type[models.TextChoices], *, title: StrOrPromise
) -> type[admin.SimpleListFilter]:
    """A sidebar filter listing ``enum``'s members by label."""

    class EnumFilter(admin.SimpleListFilter):
        parameter_name = field

        def lookups(
            self, request: HttpRequest, model_admin: Any
        ) -> list[tuple[str, str]]:
            return [(str(value), str(label)) for value, label in enum.choices]

        def queryset(
            self, request: HttpRequest, queryset: QuerySet[Any]
        ) -> QuerySet[Any]:
            if self.value():
                return queryset.filter(**{field: self.value()})
            return queryset

    EnumFilter.title = title
    EnumFilter.__name__ = f"{enum.__name__}Filter"
    return EnumFilter
