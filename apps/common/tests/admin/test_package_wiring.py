"""Per-entity admin packages: sibling-module constants reach the class."""

import importlib
import sys
import types
from importlib.machinery import ModuleSpec

import pytest
from django.contrib import admin
from django.core.exceptions import ImproperlyConfigured

from apps.common.admin import BaseModelAdmin
from apps.common.admin.package import DECLARATION_MODULES
from apps.common.admin.package import declaration_modules
from apps.users.admin.user.resource import UserResource


def test_every_package_constant_lands_on_its_admin() -> None:
    """A constant nothing consumes is exactly the bug this exists to stop."""
    checked = 0
    for admin_cls in admin.site._registry.values():
        if not isinstance(admin_cls, BaseModelAdmin):
            continue
        for module in declaration_modules(type(admin_cls)):
            for name, value in vars(module).items():
                if (
                    name.isupper()
                    and not name.startswith("_")
                    and name != "TYPE_CHECKING"
                ):
                    attribute = getattr(type(admin_cls), name.lower())
                    assert attribute is value or name.lower() in vars(type(admin_cls))
                    checked += 1
    assert checked > 100  # 13 packages x ~14 constants


def test_a_constant_with_no_matching_attribute_fails_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = types.ModuleType("probe_admin_pkg")
    package.__path__ = []  # a package with no importable children on disk
    package.__spec__ = ModuleSpec("probe_admin_pkg", None, is_package=True)
    list_view = types.ModuleType("probe_admin_pkg.list_view")
    list_view.__spec__ = ModuleSpec("probe_admin_pkg.list_view", None)
    list_view.LIST_DISPLAYY = ("id",)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "probe_admin_pkg", package)
    monkeypatch.setitem(sys.modules, "probe_admin_pkg.list_view", list_view)
    assert importlib.util.find_spec("probe_admin_pkg.list_view") is not None
    assert DECLARATION_MODULES[1] == "list_view"

    with pytest.raises(ImproperlyConfigured, match=r"LIST_DISPLAYY.*list_displayy"):

        class ProbeAdmin(BaseModelAdmin):
            __module__ = "probe_admin_pkg.admin"
            can_add = can_change = can_delete = False
            resource_classes = [UserResource]


def test_the_class_body_wins_over_the_package_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = types.ModuleType("probe_admin_pkg2")
    package.__path__ = []
    package.__spec__ = ModuleSpec("probe_admin_pkg2", None, is_package=True)
    list_view = types.ModuleType("probe_admin_pkg2.list_view")
    list_view.__spec__ = ModuleSpec("probe_admin_pkg2.list_view", None)
    list_view.LIST_PER_PAGE = 7  # type: ignore[attr-defined]
    list_view.ORDERING = ("-pk",)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "probe_admin_pkg2", package)
    monkeypatch.setitem(sys.modules, "probe_admin_pkg2.list_view", list_view)

    class ProbeAdmin(BaseModelAdmin):
        __module__ = "probe_admin_pkg2.admin"
        can_add = can_change = can_delete = False
        resource_classes = [UserResource]
        list_per_page = 99

    assert ProbeAdmin.list_per_page == 99
    assert ProbeAdmin.ordering == ("-pk",)
