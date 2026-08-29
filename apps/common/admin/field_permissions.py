from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django.conf import settings
from django.contrib.admin.options import BaseModelAdmin as DjangoBaseModelAdmin
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Model
from django.forms import ModelForm
from django.http import HttpRequest

from apps.common.admin.context import AdminContext
from apps.common.models import BaseModel

FieldRule = Callable[[AdminContext], bool]

Fieldsets = list[tuple[str | None, dict[str, Any]]]

if TYPE_CHECKING:
    _RuleBase = DjangoBaseModelAdmin[Any]
else:
    _RuleBase = DjangoBaseModelAdmin  # not subscriptable at runtime


def expand_translation_shadows(
    *, fields: tuple[str, ...], model_field_names: Collection[str]
) -> tuple[str, ...]:
    """modeltranslation shadow columns (``name`` -> ``name_ar``/``name_en``)
    are separate model fields, so a rule keyed on the base field would leak
    through them; every rule lookup expands to the shadows that exist."""
    expanded: dict[str, None] = {}
    for field in fields:
        expanded.setdefault(field, None)
        for code, _label in settings.LANGUAGES:
            shadow = f"{field}_{code.replace('-', '_')}"
            if shadow in model_field_names:
                expanded.setdefault(shadow, None)
    return tuple(expanded)


def on_change(context: AdminContext) -> bool:
    return context.is_change


class FieldPermissions:
    """Declarative field-level rules; BaseModelAdmin applies them per request.

    A field becomes readonly/hidden when its rule evaluates true:

        field_permissions = FieldPermissions(
            readonly_when={"email": on_change},
            hidden_when={"internal_note": lambda ctx: not ctx.is_superuser},
        )

    Unconditionally readonly fields are plain ``readonly_fields`` - a rule
    that always fires is not a rule.
    """

    def __init__(
        self,
        *,
        readonly_when: Mapping[str, FieldRule] | None = None,
        hidden_when: Mapping[str, FieldRule] | None = None,
    ) -> None:
        self.readonly_when = dict(readonly_when or {})
        self.hidden_when = dict(hidden_when or {})

    def readonly_fields(self, context: AdminContext) -> tuple[str, ...]:
        return tuple(
            field for field, rule in self.readonly_when.items() if rule(context)
        )

    def hidden_fields(self, context: AdminContext) -> tuple[str, ...]:
        return tuple(field for field, rule in self.hidden_when.items() if rule(context))

    def ruled_fields(self) -> frozenset[str]:
        return frozenset(self.readonly_when) | frozenset(self.hidden_when)


class FieldRuleLookups(_RuleBase):
    """The field-rule half of the admin framework, shared by BaseModelAdmin
    and the inline bases (Django's BaseModelAdmin is their common ancestor,
    so the ``super()`` calls below resolve to unfold's classes).

    - ``field_permissions`` rules are evaluated per request/object and every
      lookup expands to modeltranslation shadow columns.
    - Hiding a field needs BOTH get_exclude (removes it from the auto-built
      form) and get_fieldsets filtering (a form-less field still named in
      declared fieldsets is a KeyError at render); one path is load-bearing
      per admin depending on whether it declares fieldsets.
    - ``created_at``/``updated_at`` are readonly on every BaseModel.
    """

    model: Any
    field_permissions: ClassVar[FieldPermissions] = FieldPermissions()

    @classmethod
    def validate_field_rules(cls, model: type[Model]) -> None:
        """Every rule key must name a real field of ``model`` - a typo would
        otherwise be a rule that silently governs nothing."""
        field_names = {field.name for field in model._meta.get_fields()}
        unknown = sorted(cls.field_permissions.ruled_fields() - field_names)
        if unknown:
            msg = (
                f"{cls.__name__}: field_permissions names fields that "
                f"{model._meta.label} does not have: {', '.join(unknown)}."
            )
            raise ImproperlyConfigured(msg)

    def readonly_rule_fields(self, context: AdminContext) -> tuple[str, ...]:
        return expand_translation_shadows(
            fields=self.field_permissions.readonly_fields(context),
            model_field_names=self._model_field_names(),
        )

    def hidden_rule_fields(self, context: AdminContext) -> tuple[str, ...]:
        return expand_translation_shadows(
            fields=self.field_permissions.hidden_fields(context),
            model_field_names=self._model_field_names(),
        )

    def _model_field_names(self) -> frozenset[str]:
        return frozenset(field.name for field in self.model._meta.get_fields())

    def get_readonly_fields(
        self, request: HttpRequest, obj: Any | None = None
    ) -> tuple[str, ...]:
        context = AdminContext(request=request, obj=obj)
        # dict keys: ordered + deduplicated
        readonly = dict.fromkeys(super().get_readonly_fields(request, obj))
        readonly.update(dict.fromkeys(self.readonly_rule_fields(context)))
        if issubclass(self.model, BaseModel):
            readonly.update(dict.fromkeys(("created_at", "updated_at")))
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
        """Filter hidden fields out of declared fieldsets; drop emptied ones."""
        context = AdminContext(request=request, obj=obj)
        hidden = set(self.hidden_rule_fields(context))
        return drop_hidden_from_fieldsets(
            super().get_fieldsets(request, obj), hidden=hidden
        )

    def drop_hidden_declared_fields(
        self, form: type[ModelForm[Any]], context: AdminContext
    ) -> None:
        """get_exclude only reaches the auto-built model fields; fields a
        custom ``form`` declares survive Meta.exclude and would stay bound
        (and saved) on POST while invisible on the page."""
        for name in set(self.hidden_rule_fields(context)) & set(form.declared_fields):
            del form.base_fields[name]


def drop_hidden_from_fieldsets(
    fieldsets: Collection[tuple[Any, Any]], *, hidden: set[str]
) -> Fieldsets:
    """Filter hidden fields out of declared fieldsets; drop emptied ones."""
    filtered: Fieldsets = []
    for title, options in fieldsets:
        fields: list[str | tuple[str, ...]] = []
        for row in options["fields"]:
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
