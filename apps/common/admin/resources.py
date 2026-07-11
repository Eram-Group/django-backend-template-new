from typing import Any

from django.core.exceptions import ImproperlyConfigured
from import_export.resources import ModelResource


class BaseModelResource(ModelResource):
    """Import-export base: Meta.fields must be explicit and non-empty.

    A resource without explicit fields silently exports nothing useful -
    fail at import time instead.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        meta = getattr(cls, "Meta", None)
        if not getattr(meta, "fields", None):
            msg = f"{cls.__name__}.Meta.fields must be an explicit non-empty tuple."
            raise ImproperlyConfigured(msg)
