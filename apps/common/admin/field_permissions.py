from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Mapping
from typing import Any
from typing import ClassVar

from django.conf import settings

from apps.common.admin.context import AdminContext

FieldRule = Callable[[AdminContext], bool]

Fieldsets = list[tuple[str | None, dict[str, Any]]]


def expand_translation_shadows(
    fields: tuple[str, ...], model_field_names: Collection[str]
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


def always(context: AdminContext) -> bool:
    return True


def on_change(context: AdminContext) -> bool:
    return context.is_change


class FieldPermissions:
    """Declarative field-level rules; BaseModelAdmin applies them per request.

    A field becomes readonly/hidden when its rule evaluates true:

        field_permissions = FieldPermissions(
            readonly_when={"email": on_change},
            hidden_when={"internal_note": lambda ctx: not ctx.user.is_superuser},
        )
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


class FieldRuleLookups:
    """Rule evaluation shared by BaseModelAdmin and the inline bases.

    Consumers provide ``model`` (set by Django on admins/inlines) and may
    override ``field_permissions``. Every lookup expands to modeltranslation
    shadow columns.
    """

    model: Any
    field_permissions: ClassVar[FieldPermissions] = FieldPermissions()

    def readonly_rule_fields(self, context: AdminContext) -> tuple[str, ...]:
        return expand_translation_shadows(
            self.field_permissions.readonly_fields(context), self._model_field_names()
        )

    def hidden_rule_fields(self, context: AdminContext) -> tuple[str, ...]:
        return expand_translation_shadows(
            self.field_permissions.hidden_fields(context), self._model_field_names()
        )

    def _model_field_names(self) -> frozenset[str]:
        return frozenset(field.name for field in self.model._meta.get_fields())


def drop_hidden_from_fieldsets(
    fieldsets: Collection[tuple[Any, Any]], hidden: set[str]
) -> Fieldsets:
    """Filter hidden fields out of declared fieldsets; drop emptied ones."""
    filtered: Fieldsets = []
    for title, options in fieldsets:
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
