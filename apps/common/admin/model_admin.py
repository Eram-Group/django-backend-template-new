from collections.abc import Sequence
from typing import Any
from typing import ClassVar

from django.contrib.admin import ShowFacets
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest
from import_export.admin import ExportActionModelAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.import_export.forms import SelectableFieldsExportForm

from apps.common.admin.context import AdminContext
from apps.common.admin.field_permissions import FieldPermissions
from apps.common.admin.field_permissions import FieldRuleLookups
from apps.common.admin.field_permissions import Fieldsets
from apps.common.admin.field_permissions import drop_hidden_from_fieldsets

_REQUIRED_FLAGS = ("can_add", "can_change", "can_delete")


class BaseModelAdmin(FieldRuleLookups, ModelAdmin):
    """unfold ModelAdmin that forces every admin to state its capabilities.

    - can_add / can_change / can_delete MUST be declared on the class or an
      ancestor (loud import-time failure; intermediates that decide nothing
      set ``abstract_admin = True`` in their own body).
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
    hide_inlines_on_add: ClassVar[bool] = True
    # Declare `abstract_admin = True` in an intermediate's own body to skip
    # the can_* enforcement; the flag deliberately does NOT inherit.
    abstract_admin: ClassVar[bool] = False
    filter_horizontal: Sequence[str] = ()

    # unfold/django quality-of-life defaults
    empty_value_display = "-"
    compressed_fields = True
    warn_unsaved_form = True
    change_form_show_cancel_button = True
    show_facets = ShowFacets.ALWAYS

    def __init__(self, model: type[Model], admin_site: AdminSite) -> None:
        super().__init__(model, admin_site)
        if not self.filter_horizontal:
            # Custom-`through` M2Ms are not admin-editable and fail admin.E013
            # when listed in filter_horizontal.
            self.filter_horizontal = [
                field.name
                for field in model._meta.many_to_many
                if (through := field.remote_field.through) is not None
                and through._meta.auto_created
            ]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Django's changelist formset reads self.list_editable directly and
        # never consults per-request readonly/hidden rules, so a ruled field
        # in list_editable would be bulk-editable from the changelist.
        permissions = getattr(cls, "field_permissions", FieldPermissions())
        ruled = set(permissions.readonly_when) | set(permissions.hidden_when)
        bypassed = sorted(ruled & set(cls.list_editable or ()))
        if bypassed:
            msg = (
                f"{cls.__name__}: {', '.join(bypassed)} cannot be in "
                "list_editable - the changelist formset ignores "
                "field_permissions rules."
            )
            raise ImproperlyConfigured(msg)
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

    # --- field-level rules (lookups inherited from FieldRuleLookups) -----------
    def get_readonly_fields(
        self, request: HttpRequest, obj: Any | None = None
    ) -> tuple[str, ...]:
        context = AdminContext(request=request, obj=obj)
        # dict keys: ordered + deduplicated
        readonly = dict.fromkeys(super().get_readonly_fields(request, obj))
        readonly.update(dict.fromkeys(self.readonly_rule_fields(context)))
        model_fields = {field.name for field in self.model._meta.fields}
        for timestamp in ("created_at", "updated_at"):
            if timestamp in model_fields:
                readonly[timestamp] = None
        return tuple(readonly)

    # Hiding a field needs BOTH get_exclude (removes it from the auto-built
    # form) and get_fieldsets filtering (a form-less field still named in
    # declared fieldsets is a KeyError at render); one path is load-bearing
    # per admin depending on whether it declares fieldsets.
    def get_exclude(
        self, request: HttpRequest, obj: Any | None = None
    ) -> list[str] | None:
        context = AdminContext(request=request, obj=obj)
        excluded = list(super().get_exclude(request, obj) or ())
        excluded += [
            field for field in self.hidden_rule_fields(context) if field not in excluded
        ]
        return excluded or None

    def get_form(
        self,
        request: HttpRequest,
        obj: Any | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm[Any]]:
        """Fields a custom ``form`` declares survive Meta.exclude - without
        this they stay bound/saved on POST while invisible on the page."""
        form: type[ModelForm[Any]] = super().get_form(
            request, obj, change=change, **kwargs
        )
        context = AdminContext(request=request, obj=obj)
        for name in self.hidden_rule_fields(context):
            form.base_fields.pop(name, None)
        return form

    def get_fieldsets(self, request: HttpRequest, obj: Any | None = None) -> Fieldsets:
        """Filter hidden fields out of declared fieldsets; drop emptied ones."""
        context = AdminContext(request=request, obj=obj)
        hidden = set(self.hidden_rule_fields(context))
        fieldsets = list(super().get_fieldsets(request, obj))
        if not hidden:
            return fieldsets
        return drop_hidden_from_fieldsets(fieldsets, hidden)

    def get_list_display(self, request: HttpRequest) -> list[Any]:
        context = AdminContext(request=request, obj=None)
        hidden = set(self.hidden_rule_fields(context))
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
    """BaseModelAdmin + import-export's export action, unfold-styled.

    The selectable-fields form lets the operator pick columns per run; the
    resource's explicit Meta.fields stays the outer allowlist. Offered file
    formats come from settings.EXPORT_FORMATS (CSV + XLSX).
    """

    abstract_admin = True
    export_form_class = SelectableFieldsExportForm
