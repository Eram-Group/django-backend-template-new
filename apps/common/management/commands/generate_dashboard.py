"""Scaffold an admin module: manage.py generate_dashboard <app> <Model>.

Emits ``admin/<entity>.py`` on the apps.common.admin framework and appends the
model's export resource to ``admin/resources.py`` (created if missing). The
module is complete, real code with exactly one hole: the three capability
flags are ``...`` so the admin fails at import until a human decides them.
Existing files are never overwritten.
"""

import re
from pathlib import Path
from typing import Any

from django.apps import apps
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.core.management.base import CommandParser

ADMIN_TEMPLATE = """from django.contrib import admin

from {app_name}.admin.resources import {model}Resource
from {app_name}.models import {model}
from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions


@admin.register({model})
class {model}Admin(BaseModelAdmin):
    # Decide each one; the admin refuses to import while any is still `...`.
    can_add = ...
    can_change = ...
    can_delete = ...
    # Per-request rules: readonly_when / hidden_when keyed by field name, valued
    # by a rule (on_change, or lambda ctx: ...). Rules auto-cover modeltranslation
    # _ar/_en shadow columns. Unconditionally readonly fields belong in
    # readonly_fields instead.
    field_permissions = FieldPermissions()
    resource_classes = [{model}Resource]

    list_display = ("__str__", "created_at")
    list_filter = ()
    list_filter_submit = False
    search_fields = ()
    search_help_text = ""
    ordering = ("-created_at",)
    list_per_page = 50

    fieldsets = (
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
    readonly_fields = ()
"""

RESOURCES_HEADER = '''"""Import-export resources for {app_label} - explicit fields only.

Exports are read by non-engineers - never raw provider payloads or credentials.
"""

from apps.common.admin import BaseModelResource
'''

RESOURCE_TEMPLATE = """

class {model}Resource(BaseModelResource):
    class Meta:
        model = {model}
        fields = (
            "id",
{resource_rows}
            "created_at",
        )
"""


def _entity_name(model_name: str) -> str:
    """CamelCase -> snake_case module name (WalletTransaction -> wallet_transaction)."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", model_name).lower()


class Command(BaseCommand):
    help = "Scaffold admin/<entity>.py (+ its export resource) on apps.common.admin."

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
        admin_dir = Path(app_config.path) / "admin"
        module = admin_dir / f"{_entity_name(model.__name__)}.py"
        resources = admin_dir / "resources.py"
        if module.exists():
            msg = f"{module}: already exists - not overwriting."
            raise CommandError(msg)
        resource_class = f"class {model.__name__}Resource("
        if resources.exists() and resource_class in resources.read_text():
            msg = (
                f"{resources}: already defines {model.__name__}Resource - "
                "not overwriting."
            )
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
            "app_label": app_label,
            "model": model.__name__,
            "fieldset_rows": "\n".join(
                f'                    "{name}",' for name in concrete_fields
            ),
            "resource_rows": "\n".join(
                f'            "{name}",' for name in concrete_fields
            ),
        }
        admin_dir.mkdir(parents=True, exist_ok=True)
        if not resources.exists():
            resources.write_text(
                RESOURCES_HEADER.format(**substitutions)
                + f"from {app_config.name}.models import {model.__name__}\n"
            )
        else:
            text = resources.read_text()
            model_import = f"from {app_config.name}.models import {model.__name__}\n"
            if model_import not in text:
                # Keep the import block together: after the last import line.
                lines = text.splitlines(keepends=True)
                last = max(
                    i for i, line in enumerate(lines) if line.startswith("from ")
                )
                lines.insert(last + 1, model_import)
                text = "".join(lines)
            resources.write_text(text)
        with resources.open("a") as handle:
            handle.write(RESOURCE_TEMPLATE.format(**substitutions))
        module.write_text(ADMIN_TEMPLATE.format(**substitutions))

        self.stdout.write(
            self.style.SUCCESS(
                f"wrote {module} and {model.__name__}Resource in {resources}"
            )
        )
        self.stdout.write(
            f"Next: decide can_add/can_change/can_delete in {module.name}, then "
            f"import {model.__name__}Admin in {app_config.name}.admin (__init__.py)."
        )
