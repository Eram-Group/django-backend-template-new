"""Per-entity admin packages declare their configuration as module constants
(``permissions.CAN_ADD``, ``list_view.LIST_DISPLAY``, ``change_view.FIELDSETS``,
...); the admin class picks every one of them up by name.

No hand-copied ``list_display = list_view.LIST_DISPLAY`` lines - and no
constant can be declared in a sibling module and then silently never consumed
(that is how two changelists lost their ``list_select_related``). A constant
whose lower-cased name is not an attribute the admin class knows is an
import-time error; a value the class body sets itself always wins.
"""

import importlib
import importlib.util
from types import ModuleType

from django.core.exceptions import ImproperlyConfigured

#: The sibling modules of ``<package>/admin.py`` that hold declarations.
DECLARATION_MODULES = ("permissions", "list_view", "change_view")

#: Upper-case names a declaration module may carry that are not declarations.
_NOT_DECLARATIONS = frozenset({"TYPE_CHECKING"})


def declaration_modules(cls: type) -> list[ModuleType]:
    """The sibling declaration modules of the package ``cls`` lives in - an
    admin outside a per-entity package (a re-registered third-party admin
    in ``apps/<app>/admin/<name>.py``) has none and declares inline."""
    package = cls.__module__.rpartition(".")[0]
    modules = []
    for name in DECLARATION_MODULES:
        qualified = f"{package}.{name}"
        if importlib.util.find_spec(qualified) is not None:
            modules.append(importlib.import_module(qualified))
    return modules


def _known_attributes(cls: type) -> set[str]:
    known = {name for klass in cls.__mro__ for name in vars(klass)}
    for klass in cls.__mro__:
        known.update(getattr(klass, "__annotations__", {}))
    return known


def wire_package_declarations(cls: type) -> None:
    """Copy every ``UPPER_CASE`` constant of the sibling declaration modules
    onto ``cls`` as its lower-cased attribute, unless the class body already
    set it. Runs from ``BaseModelAdmin.__init_subclass__``."""
    known = _known_attributes(cls)
    for module in declaration_modules(cls):
        for name, value in vars(module).items():
            if not name.isupper() or name.startswith("_") or name in _NOT_DECLARATIONS:
                continue
            attribute = name.lower()
            if attribute not in known:
                msg = (
                    f"{module.__name__}.{name}: {cls.__name__} has no "
                    f"{attribute!r} attribute to receive it (typo, or a "
                    "constant nothing consumes)."
                )
                raise ImproperlyConfigured(msg)
            if attribute in vars(cls):
                continue  # the class body decides
            setattr(cls, attribute, value)
