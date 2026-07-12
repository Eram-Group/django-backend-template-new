"""Inline bases with the same discipline as BaseModelAdmin.

- can_add / can_change / can_delete MUST be declared on the class or an
  ancestor (loud import-time failure; intermediates that decide nothing set
  ``abstract_admin = True`` in their own body).
- field_permissions rules apply inside inline rows. The AdminContext they
  receive carries the PARENT object (``ctx.is_add`` = parent add view).
- Hidden on the parent's add view unless ``show_on_add = True``;
  ``show_change_link`` defaults on so child rows link to their own admin.
- ReadOnly variants are the ``can_* = False`` preset: pure display/navigation
  rows for audit-style views of related records.
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django.contrib.admin.options import InlineModelAdmin
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from unfold.admin import StackedInline
from unfold.admin import TabularInline

from apps.common.admin.context import AdminContext
from apps.common.admin.field_permissions import FieldRuleLookups
from apps.common.admin.field_permissions import drop_hidden_from_fieldsets

_REQUIRED_FLAGS = ("can_add", "can_change", "can_delete")

if TYPE_CHECKING:
    _InlineBase = InlineModelAdmin[Any, Any]
else:
    _InlineBase = InlineModelAdmin  # not subscriptable at runtime


def _declared_explicitly(cls: type, flag: str) -> bool:
    for klass in cls.__mro__:
        if klass is InlineModelAdmin:
            return False
        if flag in vars(klass):
            return True
    return False


class InlineDiscipline(FieldRuleLookups, _InlineBase):
    """Capability + field-permission enforcement shared by the inline bases."""

    # can_delete doubles as django's DELETE-checkbox flag on the formset -
    # the two meanings align (False removes the checkbox AND the permission).
    can_add: bool
    can_change: bool
    can_delete: bool
    show_on_add: ClassVar[bool] = False
    # Declare `abstract_admin = True` in an intermediate's own body to skip
    # the can_* enforcement; the flag deliberately does NOT inherit.
    abstract_admin: ClassVar[bool] = False

    extra = 0
    show_change_link = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("abstract_admin", False):
            return
        # hasattr won't do: django's InlineModelAdmin predefines can_delete,
        # so a flag only counts when declared below that base.
        missing = [
            flag for flag in _REQUIRED_FLAGS if not _declared_explicitly(cls, flag)
        ]
        if missing:
            msg = (
                f"{cls.__name__} must explicitly declare {', '.join(missing)} "
                "(or set abstract_admin = True for intermediates)."
            )
            raise ImproperlyConfigured(msg)

    # --- capability gates ---------------------------------------------------
    def has_add_permission(self, request: HttpRequest, obj: Any | None = None) -> bool:
        return self.can_add and bool(super().has_add_permission(request, obj))

    def has_change_permission(
        self, request: HttpRequest, obj: Any | None = None
    ) -> bool:
        return self.can_change and bool(super().has_change_permission(request, obj))

    def has_delete_permission(
        self, request: HttpRequest, obj: Any | None = None
    ) -> bool:
        return self.can_delete and bool(super().has_delete_permission(request, obj))

    # --- field-level rules ----------------------------------------------------
    def get_readonly_fields(
        self, request: HttpRequest, obj: Any | None = None
    ) -> tuple[str, ...]:
        context = AdminContext(request=request, obj=obj)
        readonly = dict.fromkeys(super().get_readonly_fields(request, obj))
        readonly.update(dict.fromkeys(self.readonly_rule_fields(context)))
        model_fields = {field.name for field in self.model._meta.fields}
        for timestamp in ("created_at", "updated_at"):
            if timestamp in model_fields:
                readonly[timestamp] = None
        return tuple(readonly)

    def get_exclude(
        self, request: HttpRequest, obj: Any | None = None
    ) -> list[str] | None:
        context = AdminContext(request=request, obj=obj)
        excluded = list(super().get_exclude(request, obj) or ())
        excluded += [
            field for field in self.hidden_rule_fields(context) if field not in excluded
        ]
        return excluded or None

    def get_fields(self, request: HttpRequest, obj: Any | None = None) -> Any:
        context = AdminContext(request=request, obj=obj)
        hidden = set(self.hidden_rule_fields(context))
        return [
            field for field in super().get_fields(request, obj) if field not in hidden
        ]

    def get_fieldsets(self, request: HttpRequest, obj: Any | None = None) -> Any:
        context = AdminContext(request=request, obj=obj)
        hidden = set(self.hidden_rule_fields(context))
        fieldsets = list(super().get_fieldsets(request, obj))
        if not hidden:
            return fieldsets
        return drop_hidden_from_fieldsets(fieldsets, hidden)

    def get_formset(
        self, request: HttpRequest, obj: Any | None = None, **kwargs: Any
    ) -> Any:
        """Fields a custom ``form`` declares survive exclude - pop them from
        the built formset form so hidden stays airtight (mirrors get_form on
        BaseModelAdmin)."""
        formset = super().get_formset(request, obj, **kwargs)
        context = AdminContext(request=request, obj=obj)
        for name in self.hidden_rule_fields(context):
            formset.form.base_fields.pop(name, None)
        return formset


class BaseTabularInline(InlineDiscipline, TabularInline):
    """Framework tabular inline; declare can_add/can_change/can_delete."""

    abstract_admin = True


class BaseStackedInline(InlineDiscipline, StackedInline):
    """Framework stacked inline; declare can_add/can_change/can_delete."""

    abstract_admin = True


class ReadOnlyTabularInline(BaseTabularInline):
    """Display-only child rows: navigation via show_change_link, no edits."""

    can_add = False
    can_change = False
    can_delete = False


class ReadOnlyStackedInline(BaseStackedInline):
    """Display-only child rows: navigation via show_change_link, no edits."""

    can_add = False
    can_change = False
    can_delete = False
