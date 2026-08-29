"""Inline bases with the same discipline as BaseModelAdmin.

- can_add / can_change / can_delete MUST be declared on the class or an
  ancestor (loud import-time failure; intermediates that decide nothing set
  ``abstract_admin = True`` in their own body). Display-only child rows are
  the three flags set to False.
- field_permissions rules apply inside inline rows. The AdminContext they
  receive carries the PARENT object (``ctx.is_add`` = parent add view).
- Hidden on the parent's add view unless ``show_on_add = True``;
  ``show_change_link`` is on so child rows link to their own admin.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django.contrib.admin.options import InlineModelAdmin
from django.http import HttpRequest
from unfold.admin import StackedInline
from unfold.admin import TabularInline

from apps.common.admin.context import AdminContext
from apps.common.admin.declarations import require_declared
from apps.common.admin.field_permissions import FieldRuleLookups

REQUIRED_DECLARATIONS = {"can_add": bool, "can_change": bool, "can_delete": bool}

if TYPE_CHECKING:
    _InlineBase = InlineModelAdmin[Any, Any]
else:
    _InlineBase = InlineModelAdmin  # not subscriptable at runtime


class InlineDiscipline(FieldRuleLookups, _InlineBase):
    """Capability + field-permission enforcement shared by the inline bases."""

    # can_delete doubles as django's DELETE-checkbox flag on the formset -
    # the two meanings align (False removes the checkbox AND the permission).
    can_add: bool
    can_change: bool
    can_delete: bool
    show_on_add: ClassVar[bool] = False
    # Declare `abstract_admin = True` in an intermediate's own body to skip
    # the declaration enforcement; the flag deliberately does NOT inherit.
    abstract_admin: ClassVar[bool]

    extra = 0
    show_change_link = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        require_declared(cls, REQUIRED_DECLARATIONS, base=InlineDiscipline)
        if vars(cls).get("abstract_admin") is not True:
            cls.validate_field_rules(cls.model)

    # --- capability gates ---------------------------------------------------
    def has_add_permission(self, request: HttpRequest, obj: Any | None = None) -> bool:
        return self.can_add and super().has_add_permission(request, obj)

    def has_change_permission(
        self, request: HttpRequest, obj: Any | None = None
    ) -> bool:
        return self.can_change and super().has_change_permission(request, obj)

    def has_delete_permission(
        self, request: HttpRequest, obj: Any | None = None
    ) -> bool:
        return self.can_delete and super().has_delete_permission(request, obj)

    # --- field-level rules (lookups inherited from FieldRuleLookups) -----------
    def get_formset(
        self, request: HttpRequest, obj: Any | None = None, **kwargs: Any
    ) -> Any:
        formset = super().get_formset(request, obj, **kwargs)
        self.drop_hidden_declared_fields(
            formset.form, AdminContext(request=request, obj=obj)
        )
        return formset


class BaseTabularInline(InlineDiscipline, TabularInline):
    """Framework tabular inline; declare can_add/can_change/can_delete."""

    abstract_admin = True


class BaseStackedInline(InlineDiscipline, StackedInline):
    """Framework stacked inline; declare can_add/can_change/can_delete."""

    abstract_admin = True
