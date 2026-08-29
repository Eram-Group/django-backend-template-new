"""generate_dashboard: the scaffold is complete code with one deliberate hole."""

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.admin import BaseModelAdmin
from apps.common.management.commands.generate_dashboard import TEMPLATES
from apps.users.admin.user.resource import UserResource

COMMENTED_OUT_CODE = re.compile(r"^\s*# \w+ = ")


def test_existing_package_is_an_error_not_an_overwrite() -> None:
    with pytest.raises(CommandError, match="not overwriting"):
        call_command("generate_dashboard", "users", "User")


def test_scaffold_writes_real_code_with_undecided_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apps.get_app_config("users"), "path", str(tmp_path))

    call_command("generate_dashboard", "users", "User")

    target = tmp_path / "admin" / "user"
    assert {path.name for path in target.iterdir()} == set(TEMPLATES)
    for name in TEMPLATES:
        source = (target / name).read_text()
        assert not COMMENTED_OUT_CODE.search(source), f"{name}: commented-out code"
    permissions = (target / "permissions.py").read_text()
    assert "CAN_ADD = ...\nCAN_CHANGE = ...\nCAN_DELETE = ...\n" in permissions
    change_view = (target / "change_view.py").read_text()
    assert '"email",' in change_view
    assert '"created_at", "updated_at"' in change_view
    assert '"email",' in (target / "resource.py").read_text()


def test_undecided_capabilities_fail_at_import() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"can_add.*must be decided"):

        class UndecidedAdmin(BaseModelAdmin):
            can_add = ...  # type: ignore[assignment]  # the scaffold's hole
            can_change = False
            can_delete = False
            resource_classes = [UserResource]
