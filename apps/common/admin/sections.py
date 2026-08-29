"""Changelist record sections (unfold ``list_sections``).

A section renders an expandable related-record preview under each changelist
row. unfold's TableSection reads ``instance.<related_name>.all()`` raw -
every related row, model order - so this base adds the two things a real
table needs: ordering and a row cap. Both MUST be declared by every section.
"""

from types import SimpleNamespace
from typing import Any
from typing import ClassVar

from unfold.sections import TableSection

from apps.common.admin.declarations import require_declared

REQUIRED_DECLARATIONS = {"ordering": tuple, "limit": int}


class LimitedTableSection(TableSection):
    """TableSection that orders and caps the related rows it renders."""

    ordering: ClassVar[tuple[str, ...]]
    limit: ClassVar[int]
    # unfold sets these in an untyped __init__/class body.
    instance: Any
    related_name: Any

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        require_declared(cls, REQUIRED_DECLARATIONS, base=LimitedTableSection)

    def render(self) -> str:
        related = getattr(self.instance, self.related_name).all()
        rows = related.order_by(*self.ordering)[: self.limit]
        original = self.instance
        # unfold's render only reads instance.<related_name>; hand it the
        # prepared queryset instead of the raw manager.
        self.instance = SimpleNamespace(**{str(self.related_name): rows})
        try:
            return str(super().render())
        finally:
            self.instance = original
