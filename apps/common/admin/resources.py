from typing import Any

from django.core.exceptions import ImproperlyConfigured
from import_export.resources import ModelResource


class BaseModelResource(ModelResource):
    """Import-export base: Meta.fields must be an explicit, non-empty tuple.

    Exports go to non-engineers and can carry secrets: the column list is a
    decision, never "whatever the model has" - fail at import time instead.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        fields = cls.Meta.fields
        if not fields or fields == "__all__":
            msg = f"{cls.__name__}.Meta.fields must be an explicit non-empty tuple."
            raise ImproperlyConfigured(msg)
