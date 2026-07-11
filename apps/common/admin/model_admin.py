from typing import Any
from typing import ClassVar

from django.contrib.admin import ShowFacets
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.http import HttpRequest
from import_export.admin import ExportActionModelAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.import_export.forms import ExportForm

from apps.common.admin.context import AdminContext
from apps.common.admin.field_permissions import FieldPermissions

_REQUIRED_FLAGS = ("can_add", "can_change", "can_delete")

_Fieldsets = list[tuple[str | None, dict[str, Any]]]


class BaseModelAdmin(ModelAdmin):
    """unfold ModelAdmin that forces every admin to state its capabilities.

    - can_add / can_change / can_delete MUST be declared (loud import-time
      failure; intermediates set ``abstract_admin = True`` in their own body).
      Per-OBJECT decisions: override has_change_permission/has_delete_permission.
    - field_permissions rules shape the form AND the declared fieldsets AND
      list_display per request/object (state-conditional views: use
      ctx.is_add / ctx.is_change in a hidden_when rule). Emptied fieldsets
      are dropped.
    - created_at/updated_at are always readonly; inlines are hidden on the
      add view unless they set show_on_add; M2M fields get the horizontal
      widget automatically unless filter_horizontal is set explicitly.
    """

    can_add: ClassVar[bool]
    can_change: ClassVar[bool]
    can_delete: ClassVar[bool]
    field_permissions: ClassVar[FieldPermissions] = FieldPermissions()
    hide_inlines_on_add: ClassVar[bool] = True
    # Declare `abstract_admin = True` in an intermediate's own body to skip
    # the can_* enforcement; the flag deliberately does NOT inherit.
    abstract_admin: ClassVar[bool] = False

    # unfold/django quality-of-life defaults
    empty_value_display = "-"
    compressed_fields = True
    warn_unsaved_form = True
    change_form_show_cancel_button = True
    show_facets = ShowFacets.ALWAYS

    def __init__(self, model: type[Model], admin_site: AdminSite) -> None:
        super().__init__(model, admin_site)
        if not self.filter_horizontal:
            self.filter_horizontal = [field.name for field in model._meta.many_to_many]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__dict__.get("abstract_admin", False):
            return
        missing = [flag for flag in _REQUIRED_FLAGS if not hasattr(cls, flag)]
        if missing:
            msg = (
                f"{cls.__name__} must explicitly declare {', '.join(missing)} "
                "(or set abstract_admin = True for intermediates)."
            )
            raise ImproperlyConfigured(msg)

    # --- capability gates ---------------------------------------------------
    def has_add_permission(self, request: HttpRequest) -> bool:
        return self.can_add and bool(super().has_add_permission(request))

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
        # dict keys: ordered + deduplicated
        readonly = dict.fromkeys(super().get_readonly_fields(request, obj))
        readonly.update(dict.fromkeys(self.field_permissions.readonly_fields(context)))
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
            field
            for field in self.field_permissions.hidden_fields(context)
            if field not in excluded
        ]
        return excluded or None

    def get_fieldsets(self, request: HttpRequest, obj: Any | None = None) -> _Fieldsets:
        """Filter hidden fields out of declared fieldsets; drop emptied ones."""
        context = AdminContext(request=request, obj=obj)
        hidden = set(self.field_permissions.hidden_fields(context))
        if not hidden:
            return list(super().get_fieldsets(request, obj))
        filtered: _Fieldsets = []
        for title, options in super().get_fieldsets(request, obj):
            fields: list[str | tuple[str, ...]] = []
            for row in options.get("fields", ()):
                if isinstance(row, str):
                    if row not in hidden:
                        fields.append(row)
                    continue
                kept = tuple(field for field in row if field not in hidden)
                if kept:
                    fields.append(kept)
            if fields:
                filtered.append((title, {**options, "fields": tuple(fields)}))
        return filtered

    def get_list_display(self, request: HttpRequest) -> list[Any]:
        context = AdminContext(request=request, obj=None)
        hidden = set(self.field_permissions.hidden_fields(context))
        return [
            column
            for column in super().get_list_display(request)
            if not (isinstance(column, str) and column in hidden)
        ]

    # --- inlines -----------------------------------------------------------------
    def get_inlines(self, request: HttpRequest, obj: Any | None = None) -> list[Any]:
        inlines = list(super().get_inlines(request, obj))
        if obj is None and self.hide_inlines_on_add:
            return [
                inline for inline in inlines if getattr(inline, "show_on_add", False)
            ]
        return inlines


class ExportableModelAdmin(BaseModelAdmin, ExportActionModelAdmin):
    """BaseModelAdmin + import-export's export action, unfold-styled."""

    abstract_admin = True
    export_form_class = ExportForm
