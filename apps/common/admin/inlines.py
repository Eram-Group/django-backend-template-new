from typing import ClassVar

from unfold.admin import StackedInline
from unfold.admin import TabularInline


class BaseTabularInline(TabularInline):
    """Hidden on the add view unless the inline sets show_on_add = True."""

    show_on_add: ClassVar[bool] = False
    extra = 0


class BaseStackedInline(StackedInline):
    """Hidden on the add view unless the inline sets show_on_add = True."""

    show_on_add: ClassVar[bool] = False
    extra = 0
