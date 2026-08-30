"""Change-form configuration for Zone.

modeltranslation expands ``name`` into its ar/en tab pair. FIELDSETS carries
the explicit annotation because TabbedTranslationAdmin is a typed base (see
the country admin). ``geometry`` itself is never a form field - PostGIS
geometry is loaded from files, the form shows a readonly summary instead.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldsetSpec

FIELDSETS: _FieldsetSpec = (
    (None, {"fields": ("country", "code", "region_code", "name", "is_active")}),
    ("Geometry", {"fields": ("geometry_details",)}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
# Identity comes from the file at load time, never retyped.
READONLY_FIELDS = ("country", "code", "geometry_details")
