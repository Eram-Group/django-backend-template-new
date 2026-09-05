"""generate_dashboard: the scaffold is complete code with one deliberate hole."""

import re
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.admin import BaseModelAdmin
from apps.users.admin.resources import UserResource

COMMENTED_OUT_CODE = re.compile(r"^\s*# \w+ = ")


def test_existing_module_is_an_error_not_an_overwrite() -> None:
    with pytest.raises(CommandError, match="not overwriting"):
        call_command("generate_dashboard", "users", "User")


def test_scaffold_writes_real_code_with_undecided_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apps.get_app_config("users"), "path", str(tmp_path))

    call_command("generate_dashboard", "users", "User")

    admin_dir = tmp_path / "admin"
    assert {path.name for path in admin_dir.iterdir()} == {"user.py", "resources.py"}
    module = (admin_dir / "user.py").read_text()
    assert not COMMENTED_OUT_CODE.search(module), "commented-out code"
    assert "    can_add = ...\n    can_change = ...\n    can_delete = ...\n" in module
    assert '"email",' in module
    assert '"created_at", "updated_at"' in module
    resources = (admin_dir / "resources.py").read_text()
    assert "class UserResource(BaseModelResource):" in resources
    assert '"email",' in resources


def test_scaffold_appends_to_an_existing_resources_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(apps.get_app_config("notifications"), "path", str(tmp_path))
    call_command("generate_dashboard", "notifications", "Device")

    call_command("generate_dashboard", "notifications", "NotificationDelivery")

    resources = (tmp_path / "admin" / "resources.py").read_text()
    assert resources.count("from apps.common.admin import BaseModelResource") == 1
    assert "class DeviceResource(" in resources
    assert "class NotificationDeliveryResource(" in resources
    assert (tmp_path / "admin" / "notification_delivery.py").exists()  # snake_case


def test_undecided_capabilities_fail_at_import() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"can_add.*must be decided"):

        class UndecidedAdmin(BaseModelAdmin):
            can_add = ...  # type: ignore[assignment]  # the scaffold's hole
            can_change = False
            can_delete = False
            resource_classes = [UserResource]
