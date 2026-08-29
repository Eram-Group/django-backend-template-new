"""Import-time "declare it or fail" enforcement shared by the admin bases."""

from collections.abc import Mapping

from django.core.exceptions import ImproperlyConfigured


def declared_below(cls: type, name: str, *, base: type) -> bool:
    """True when ``name`` is set in the body of ``cls`` or an ancestor that
    sits BELOW ``base`` in the MRO. ``hasattr`` will not do: Django's own
    admin classes predefine some of the flags (``InlineModelAdmin.can_delete``),
    so an inherited library value must not count as a decision."""
    for klass in cls.__mro__:
        if klass is base:
            return False
        if name in vars(klass):
            return True
    return False


def require_declared(cls: type, names: Mapping[str, type], *, base: type) -> None:
    """Loud import-time failure unless every name is declared below ``base``
    with a value of the expected type (a scaffolded ``...`` placeholder is
    not a decision). ``abstract_admin = True`` in a class's OWN body (never
    inherited) opts an intermediate out."""
    if vars(cls).get("abstract_admin") is True:
        return
    missing = [name for name in names if not declared_below(cls, name, base=base)]
    if missing:
        msg = (
            f"{cls.__name__} must explicitly declare {', '.join(missing)} "
            "(or set abstract_admin = True for intermediates)."
        )
        raise ImproperlyConfigured(msg)
    undecided = [
        name for name, kind in names.items() if not isinstance(getattr(cls, name), kind)
    ]
    if undecided:
        msg = (
            f"{cls.__name__}: {', '.join(undecided)} must be decided "
            f"({', '.join(f'{name}: {names[name].__name__}' for name in undecided)})."
        )
        raise ImproperlyConfigured(msg)
