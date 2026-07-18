"""Scaffold a per-entity admin package: manage.py generate_dashboard <app> <Model>.

Emits admin/<entity>/{__init__,admin,list_view,change_view,display,
permissions,resource,inline}.py + CHECKLIST.md wired to the
apps.common.admin framework. Existing files are skipped unless --overwrite.
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

CAN_ADD = False  # decide deliberately, then flip
CAN_CHANGE = False
CAN_DELETE = False

# One line per concrete field - uncomment and attach a rule (on_change,
# always, or lambda ctx: ...) for every field that needs one. Rules
# auto-cover modeltranslation _ar/_en shadow columns.
FIELD_PERMISSIONS = FieldPermissions(
    readonly_when={{
{field_rules}
    }},
    # hidden_when={{"secret_field": lambda ctx: not ctx.is_superuser}},
)
''',
    "list_view.py": '''"""Changelist configuration for {model}."""

LIST_DISPLAY = ("__str__", "created_at")
LIST_FILTER = (
    # Date-range filtering: ("created_at", RangeDateFilter) from
    # unfold.contrib.filters.admin - then flip LIST_FILTER_SUBMIT.
)
LIST_FILTER_SUBMIT = False  # form-based (range) filters apply on submit
SEARCH_FIELDS = ()
# When SEARCH_FIELDS lands: wrap in gettext_lazy and say what search matches.
SEARCH_HELP_TEXT = ""
ORDERING = ("-created_at",)
LIST_PER_PAGE = 50
''',
    "inline.py": '''"""Inline for embedding {model} rows on a parent admin.

Decide: does {model} appear inline anywhere? If yes, fill this in and add
it to the parent admin's `inlines`; if not, delete the file and tick the
CHECKLIST line either way.
"""

from {app_name}.models import {model}
from apps.common.admin import BaseTabularInline


class {model}Inline(BaseTabularInline):
    """{model} rows under a parent's change form."""

    model = {model}
    can_add = False  # decide deliberately, then flip
    can_change = False
    can_delete = False
    fields = ()  # explicit, like the resource
    show_on_add = False  # inlines hide on the parent's add view by default
    # Display-only rows? subclass ReadOnlyTabularInline instead.
''',
    "change_view.py": '''"""Change-form configuration for {model}."""

FIELDSETS = (
    # (None, {{"fields": (...)}}),
)
READONLY_FIELDS = ()
''',
    "display.py": '''"""Computed display columns for {model}.

Every computed column must carry ordering= (changelist sorting keeps
working) and a translated description. Prefer unfold's display decorator -
it adds avatar header rows and colored label badges on top of django's:

    from unfold.decorators import display


    @display(description=_("Example"), header=True, ordering="created_at")
    def example_header(obj: {model}) -> list[object]:
        return [str(obj), obj.created_at, "XX"]  # title, subtitle, initials


    @display(
        description=_("State"),
        ordering="created_at",
        label={{"True": "success", "False": "danger"}},
    )
    def example_badge(obj: {model}) -> str:
        return str(obj)
"""
''',
    "resource.py": '''"""Import-export resource for {model} (explicit fields only)."""

from {app_name}.models import {model}
from apps.common.admin import BaseModelResource


class {model}Resource(BaseModelResource):
    # Exports are read by non-engineers - rename columns and format dates:
    #   from import_export.fields import Field
    #   from import_export.widgets import DateTimeWidget
    #
    #   created_at = Field(
    #       attribute="created_at",
    #       column_name="Created At",
    #       widget=DateTimeWidget(format="%Y-%m-%d %H:%M:%S"),
    #   )
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
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS

    # inlines = [...]        # child rows on the change form (inline.py)
    # list_sections = [...]  # expandable per-row previews (LimitedTableSection)
    # actions_detail = [...] # state-transition buttons - the body calls a
    #                        # service, never obj.save() (see ARCHITECTURE.md)
""",
    "CHECKLIST.md": """# {model} admin checklist

- [ ] permissions: can_add / can_change / can_delete deliberately decided
- [ ] permissions: FieldPermissions rules per field reviewed (stubs emitted)
- [ ] list_view: list_display / search_fields / list_filter / ordering
- [ ] list_view: SEARCH_HELP_TEXT translated once search_fields exist
- [ ] inline: decided if {model} embeds on a parent admin (fill or delete inline.py)
- [ ] change_view: fieldsets cover every editable field; readonly rules
- [ ] display: computed columns carry ordering= (unfold header/label welcome)
- [ ] resource: export fields explicit and reviewed (no secrets)
- [ ] considered: list_sections previews / actions_detail workflow buttons
- [ ] registered import: {app_name}/admin/__init__.py imports {model}Admin
- [ ] admin-basics gate green (G07)
""",
}


class Command(BaseCommand):
    help = "Scaffold an admin/<entity>/ package wired to apps.common.admin."

    def add_arguments(self, parser: CommandParser) -> None:
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

        concrete_fields = [
            field.name
            for field in model._meta.get_fields()
            if getattr(field, "concrete", False) and not field.auto_created
        ]
        substitutions = {
            "app_name": app_config.name,
            "entity": entity,
            "model": model.__name__,
            "field_rules": "\n".join(
                f'        # "{name}": on_change,' for name in concrete_fields
            ),
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
