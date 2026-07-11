from collections.abc import Callable
from collections.abc import Mapping

from apps.common.admin.context import AdminContext

FieldRule = Callable[[AdminContext], bool]


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
