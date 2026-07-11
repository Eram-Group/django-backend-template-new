"""Scaffold a per-entity admin package: manage.py generate_dashboard <app> <Model>.

Emits admin/<entity>/{__init__,admin,list_view,change_view,display,
permissions,resource}.py + CHECKLIST.md wired to the apps.common.admin
framework. Existing files are skipped unless --overwrite.
"""

from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

TEMPLATES: dict[str, str] = {
    "__init__.py": """from {app_name}.admin.{entity}.admin import {model}Admin

__all__ = ["{model}Admin"]
""",
    "permissions.py": '''"""Capability + field decisions for the {model} admin."""

from apps.common.admin import FieldPermissions

CAN_ADD = False  # decide deliberately, then flip
CAN_CHANGE = False
CAN_DELETE = False

FIELD_PERMISSIONS = FieldPermissions(
    # readonly_when={{"some_field": on_change}},
    # hidden_when={{"secret_field": lambda ctx: not ctx.user.is_superuser}},
)
''',
    "list_view.py": '''"""Changelist configuration for {model}."""

LIST_DISPLAY = ("__str__", "created_at")
LIST_FILTER = ()
SEARCH_FIELDS = ()
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
''',
    "change_view.py": '''"""Change-form configuration for {model}."""

FIELDSETS = (
    # (None, {{"fields": (...)}}),
)
READONLY_FIELDS = ()
''',
    "display.py": '''"""Computed display columns for {model}.

Every computed column must carry @admin.display(ordering=..., description=...)
so changelist sorting keeps working.
"""

# from django.contrib.admin import display
#
#
# @display(description="Example", ordering="created_at")
# def example_column(obj: object) -> str:
#     return str(obj)
''',
    "resource.py": '''"""Import-export resource for {model} (explicit fields only)."""

from {app_name}.models import {model}
from apps.common.admin import BaseModelResource


class {model}Resource(BaseModelResource):
    class Meta:
        model = {model}
        fields = ("id", "created_at")
''',
    "admin.py": """from django.contrib import admin

from {app_name}.admin.{entity} import change_view
from {app_name}.admin.{entity} import list_view
from {app_name}.admin.{entity} import permissions
from {app_name}.admin.{entity}.resource import {model}Resource
from {app_name}.models import {model}
from apps.common.admin import ExportableModelAdmin


@admin.register({model})
class {model}Admin(ExportableModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [{model}Resource]

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    search_fields = list_view.SEARCH_FIELDS
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS
""",
    "CHECKLIST.md": """# {model} admin checklist

- [ ] permissions: can_add / can_change / can_delete deliberately decided
- [ ] permissions: FieldPermissions rules for sensitive fields
- [ ] list_view: list_display / search_fields / list_filter / ordering
- [ ] change_view: fieldsets cover every editable field; readonly rules
- [ ] display: computed columns carry admin_order_field
- [ ] resource: export fields explicit and reviewed (no secrets)
- [ ] registered import: {app_name}/admin/__init__.py imports {model}Admin
- [ ] admin-basics gate green (G07)
""",
}


class Command(BaseCommand):
    help = "Scaffold an admin/<entity>/ package wired to apps.common.admin."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("app_label", help="App label, e.g. users")
        parser.add_argument("model_name", help="Model class name, e.g. User")
        parser.add_argument(
            "--overwrite",
            action="store_true",
            help="Replace existing files instead of skipping them.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        app_label: str = options["app_label"]
        model_name: str = options["model_name"]
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError as exc:
            raise CommandError(str(exc)) from exc

        app_config = apps.get_app_config(app_label)
        entity = model.__name__.lower()
        target = Path(app_config.path) / "admin" / entity
        target.mkdir(parents=True, exist_ok=True)

        substitutions = {
            "app_name": app_config.name,
            "entity": entity,
            "model": model.__name__,
        }
        written, skipped = [], []
        for filename, template in TEMPLATES.items():
            path = target / filename
            if path.exists() and not options["overwrite"]:
                skipped.append(filename)
                continue
            path.write_text(template.format(**substitutions))
            written.append(filename)

        if written:
            self.stdout.write(
                self.style.SUCCESS(f"{target}: wrote {', '.join(written)}")
            )
        if skipped:
            self.stdout.write(f"skipped (exists, no --overwrite): {', '.join(skipped)}")
        self.stdout.write(
            f"Next: flesh out the package, import {model.__name__}Admin in "
            f"{app_config.name}.admin (__init__.py), then work the CHECKLIST."
        )
