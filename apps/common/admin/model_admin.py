from typing import Any
from typing import ClassVar
from typing import cast

from django.contrib.admin import ModelAdmin as DjangoModelAdmin
from django.contrib.admin import ShowFacets
from django.contrib.admin.options import Action
from django.contrib.admin.options import ActionLocation
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _
from import_export.admin import ExportActionModelAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.import_export.forms import SelectableFieldsExportForm

from apps.common.admin.context import AdminContext
from apps.common.admin.declarations import require_declared
from apps.common.admin.field_permissions import FieldRuleLookups
from apps.common.admin.inlines import InlineDiscipline
from apps.common.admin.resources import BaseModelResource

REQUIRED_DECLARATIONS = {
    "can_add": bool,
    "can_change": bool,
    "can_delete": bool,
    "resource_classes": list,
}


class BaseModelAdmin(FieldRuleLookups, ModelAdmin, ExportActionModelAdmin):
    """unfold ModelAdmin that forces every admin to state its capabilities.

    - can_add / can_change / can_delete / resource_classes MUST be declared
      on the class or an ancestor (loud import-time failure; intermediates
      that decide nothing set ``abstract_admin = True`` in their own body).
      Per-OBJECT decisions: override has_change_permission/has_delete_permission.
    - field_permissions rules shape the form AND the declared fieldsets AND
      list_display per request/object (state-conditional views: use
      ctx.is_add / ctx.is_change in a hidden_when rule). Emptied fieldsets
      are dropped. Every rule key is validated against the model at
      registration.
    - created_at/updated_at are always readonly; inlines are hidden on the
      add view unless they set show_on_add; M2M fields get the horizontal
      widget (custom-``through`` M2Ms are not admin-editable and fail
      admin.E013 when listed).
    - Export: import-export's export action, unfold-styled. The
      selectable-fields form lets the operator pick columns per run; the
      resource's explicit Meta.fields stays the outer allowlist. Offered
      file formats come from settings.EXPORT_FORMATS.
    """

    can_add: ClassVar[bool]
    can_change: ClassVar[bool]
    can_delete: ClassVar[bool]
    resource_classes: ClassVar[list[type[BaseModelResource]]]
    # Declare `abstract_admin = True` in an intermediate's own body to skip
    # the declaration enforcement; the flag deliberately does NOT inherit.
    abstract_admin: ClassVar[bool]
    # django-stubs types this with a TypedDict that the plain dict literals in
    # change_view modules do not satisfy; Django's admin checks validate the
    # shape at startup (admin.E0xx) - keep the modules literal.
    fieldsets: ClassVar[Any]

    export_form_class = SelectableFieldsExportForm

    # unfold/django quality-of-life defaults
    empty_value_display = "-"
    warn_unsaved_form = True
    change_form_show_cancel_button = True
    show_facets = ShowFacets.ALWAYS

    def __init__(self, model: type[Model], admin_site: AdminSite) -> None:
        # mypy sees FieldRuleLookups' typed base (django's BaseModelAdmin,
        # whose __init__ takes nothing) before unfold's untyped ModelAdmin;
        # at runtime the MRO reaches ModelAdmin.__init__(model, admin_site).
        super().__init__(model, admin_site)  # type: ignore[call-arg]
        self.validate_field_rules(model)
        # Per admin, from the model (django-stubs declares it a ClassVar).
        self.filter_horizontal = [  # type: ignore[misc]
            field.name
            for field in model._meta.many_to_many
            # ``through`` is typed Optional but always set once models load.
            if (through := field.remote_field.through) is not None
            and through._meta.auto_created
        ]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Django's changelist formset reads self.list_editable directly and
        # never consults per-request readonly/hidden rules, so a ruled field
        # in list_editable would be bulk-editable from the changelist.
        bypassed = sorted(cls.field_permissions.ruled_fields() & set(cls.list_editable))
        if bypassed:
            msg = (
                f"{cls.__name__}: {', '.join(bypassed)} cannot be in "
                "list_editable - the changelist formset ignores "
                "field_permissions rules."
            )
            raise ImproperlyConfigured(msg)
        require_declared(cls, REQUIRED_DECLARATIONS, base=BaseModelAdmin)

    # --- capability gates ---------------------------------------------------
    def has_add_permission(self, request: HttpRequest) -> bool:
        return self.can_add and super().has_add_permission(request)

    def has_change_permission(
        self, request: HttpRequest, obj: Any | None = None
    ) -> bool:
        return self.can_change and super().has_change_permission(request, obj)

    def has_delete_permission(
        self, request: HttpRequest, obj: Any | None = None
    ) -> bool:
        return self.can_delete and super().has_delete_permission(request, obj)

    # --- field-level rules (lookups inherited from FieldRuleLookups) -----------
    def get_form(
        self,
        request: HttpRequest,
        obj: Any | None = None,
        change: bool = False,
        **kwargs: Any,
    ) -> type[ModelForm[Any]]:
        form: type[ModelForm[Any]] = super().get_form(
            request, obj, change=change, **kwargs
        )
        self.drop_hidden_declared_fields(form, AdminContext(request=request, obj=obj))
        return form

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
        inlines = cast(
            "list[type[InlineDiscipline]]", list(super().get_inlines(request, obj))
        )
        if obj is None:
            return [inline for inline in inlines if inline.show_on_add]
        return inlines

    # TODO(unfold>0.104.1): delete once unfold's get_action_choices moves to
    # Django 6.1's signature. Django threads an `action_location` through the
    # admin action hooks so actions can be offered on the change FORM as well
    # as the change list, and warns about overrides still on the old
    # signature. unfold 0.104.1's get_action_choices() is one such override -
    # it exists only to relabel the blank choice - so we restate that one
    # behaviour here on the modern signature and call Django's implementation
    # directly, deliberately stepping over unfold's. Without this, Django
    # takes its deprecation path, which silently DROPS every change-form
    # action.
    def get_action_choices(
        self,
        request: HttpRequest,
        default_choices: list[tuple[str, str]] | None = None,
        action_location: ActionLocation = ActionLocation.CHANGE_LIST,
    ) -> list[tuple[str, str]]:
        if default_choices is None:
            # django-stubs types these labels as plain `str`; Django resolves
            # lazy proxies at render time, which is what the Arabic-first rule
            # needs - cast rather than evaluate the translation eagerly.
            default_choices = [("", cast("str", _("Select action")))]
        return DjangoModelAdmin.get_action_choices(
            self,
            request,
            default_choices=default_choices,
            action_location=action_location,
        )

    # TODO(django-import-export>4.4.1): delete once ExportActionMixin's
    # get_actions() moves to Django 6.1's signature. Same story as
    # get_action_choices above: 4.4.1 still has the pre-6.1 signature and
    # registers its action as a bare 3-tuple. Restate it here on the modern
    # signature, as an Action object, and pin it to the change list
    # (exporting is a bulk operation - it has no meaning on a single-object
    # form).
    def get_actions(
        self,
        request: HttpRequest,
        action_location: ActionLocation = ActionLocation.CHANGE_LIST,
    ) -> dict[str, Action | None]:
        actions = DjangoModelAdmin.get_actions(
            self, request, action_location=action_location
        )
        if action_location is ActionLocation.CHANGE_LIST and self.has_export_permission(
            request
        ):
            # Lazy on purpose (see get_action_choices above); Django applies the
            # %(verbose_name_plural)s interpolation per request, in the active
            # locale.
            description = cast("str", _("Export selected %(verbose_name_plural)s"))
            actions["export_admin_action"] = Action(
                func=type(self).export_admin_action,
                name="export_admin_action",
                description=description,
                plural_description=description,
                locations=[ActionLocation.CHANGE_LIST],
            )
        return actions
