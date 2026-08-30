"""Change-form configuration for Country.

modeltranslation expands ``name`` into its ar/en tab pair. FIELDSETS carries
the explicit annotation because TabbedTranslationAdmin is a typed base (see
the notifications kind-config admin).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldsetSpec

FIELDSETS: _FieldsetSpec = (
    (None, {"fields": ("code", "alpha_3", "name", "flag", "is_active")}),
    ("Phone", {"fields": ("dial_code", "phone_example", "max_phone_length")}),
    ("Currency", {"fields": ("currency",)}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
# ISO-derived identity: copied from the libraries at load, never retyped.
READONLY_FIELDS = (
    "code",
    "alpha_3",
    "dial_code",
    "phone_example",
    "max_phone_length",
    "currency",
)
