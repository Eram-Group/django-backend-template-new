from collections.abc import Callable
from collections.abc import Collection
from collections.abc import Mapping

from django.conf import settings

from apps.common.admin.context import AdminContext

FieldRule = Callable[[AdminContext], bool]


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
