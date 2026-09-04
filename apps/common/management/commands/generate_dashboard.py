"""Scaffold a per-entity admin package: manage.py generate_dashboard <app> <Model>.

Emits admin/<entity>/{__init__,admin,list_view,change_view,permissions,
resource}.py + CHECKLIST.md on the apps.common.admin framework (the admin
class picks the sibling modules' constants up by name). The
package is complete, real code with exactly one hole: the three capability
flags are ``...`` so the admin fails at import until a human decides them.
Existing files are never overwritten.
"""

from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser

TEMPLATES: dict[str, str] = {
    "__init__.py": """from {app_name}.admin.{entity}.admin import {model}Admin

__all__ = ["{model}Admin"]
""",
    "permissions.py": '''"""Capability + field decisions for the {model} admin."""

from apps.common.admin import FieldPermissions

# Decide each one; the admin refuses to import while any is still `...`.
CAN_ADD = ...
CAN_CHANGE = ...
CAN_DELETE = ...

# Per-request rules: readonly_when / hidden_when keyed by field name, valued
# by a rule (on_change, or lambda ctx: ...). Rules auto-cover modeltranslation
# _ar/_en shadow columns. Unconditionally readonly fields belong in
# change_view.READONLY_FIELDS instead.
FIELD_PERMISSIONS = FieldPermissions()
''',
    "list_view.py": '''"""Changelist configuration for {model}."""

LIST_DISPLAY = ("__str__", "created_at")
LIST_FILTER = ()
LIST_FILTER_SUBMIT = False
SEARCH_FIELDS = ()
SEARCH_HELP_TEXT = ""
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
''',
    "change_view.py": '''"""Change-form configuration for {model}."""

FIELDSETS = (
    (
        None,
        {{
            "fields": (
{fieldset_rows}
            )
        }},
    ),
    ("Dates", {{"fields": ("created_at", "updated_at")}}),
)
READONLY_FIELDS = ()
''',
    "resource.py": '''"""Import-export resource for {model} (explicit fields only)."""

from {app_name}.models import {model}
from apps.common.admin import BaseModelResource


class {model}Resource(BaseModelResource):
    class Meta:
        model = {model}
        fields = (
            "id",
{resource_rows}
            "created_at",
        )
''',
    "admin.py": """from django.contrib import admin

from {app_name}.admin.{entity}.resource import {model}Resource
from {app_name}.models import {model}
from apps.common.admin import BaseModelAdmin


@admin.register({model})
class {model}Admin(BaseModelAdmin):
    # permissions.py / list_view.py / change_view.py constants land here by
    # name (apps.common.admin.package); this body holds behaviour only.
    resource_classes = [{model}Resource]
""",
    "CHECKLIST.md": """# {model} admin checklist

- [ ] permissions: CAN_ADD / CAN_CHANGE / CAN_DELETE decided (undecided = import error)
- [ ] permissions: FieldPermissions rules for conditionally readonly/hidden fields
- [ ] list_view: list_display / search_fields (+ translated help text) / filters
- [ ] change_view: fieldsets reviewed; always-readonly fields in READONLY_FIELDS
- [ ] resource: export fields reviewed (no secrets)
- [ ] if {model} embeds on a parent admin, add an inline by hand (Base*Inline)
- [ ] registered import: {app_name}/admin/__init__.py imports {model}Admin
""",
}


class Command(BaseCommand):
    help = "Scaffold an admin/<entity>/ package wired to apps.common.admin."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("app_label", help="App label, e.g. users")
        parser.add_argument("model_name", help="Model class name, e.g. User")

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
        existing = sorted(name for name in TEMPLATES if (target / name).exists())
        if existing:
            msg = f"{target}: already has {', '.join(existing)} - not overwriting."
            raise CommandError(msg)

        concrete_fields = [
            field.name
            for field in model._meta.get_fields()
            if field.concrete
            and not field.auto_created
            and field.name not in ("created_at", "updated_at")
        ]
        substitutions = {
            "app_name": app_config.name,
            "entity": entity,
            "model": model.__name__,
            "fieldset_rows": "\n".join(
                f'                "{name}",' for name in concrete_fields
            ),
            "resource_rows": "\n".join(
                f'            "{name}",' for name in concrete_fields
            ),
        }
        target.mkdir(parents=True)
        for filename, template in TEMPLATES.items():
            (target / filename).write_text(template.format(**substitutions))

        self.stdout.write(self.style.SUCCESS(f"{target}: wrote {', '.join(TEMPLATES)}"))
        self.stdout.write(
            f"Next: decide permissions.py, import {model.__name__}Admin in "
            f"{app_config.name}.admin (__init__.py), then work the CHECKLIST."
        )
