"""Changelist record sections (unfold ``list_sections``).

A section renders an expandable related-record preview under each changelist
row. unfold's TableSection reads ``instance.<related_name>.all()`` raw -
every related row, model order - so this base adds the two things a real
table needs: ordering and a row cap.
"""

from types import SimpleNamespace
from typing import Any
from typing import ClassVar

from unfold.sections import TableSection


class LimitedTableSection(TableSection):
    """TableSection that orders and caps the related rows it renders."""

    ordering: ClassVar[tuple[str, ...]] = ("-pk",)
    limit: ClassVar[int] = 5
    # unfold sets these in an untyped __init__/class body.
    instance: Any
    related_name: Any

    def render(self) -> str:
        related = getattr(self.instance, self.related_name).all()
        if self.ordering:
            related = related.order_by(*self.ordering)
        rows = related[: self.limit]
        original = self.instance
        # unfold's render only reads instance.<related_name>; hand it the
        # prepared queryset instead of the raw manager.
        self.instance = SimpleNamespace(**{str(self.related_name): rows})
        try:
            return str(super().render())
        finally:
            self.instance = original
