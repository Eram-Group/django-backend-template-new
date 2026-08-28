"""Change-form configuration for NotificationKindConfig.

The standard change form is the no-JS fallback behind the single-page editor.
modeltranslation expands ``title``/``body`` into their ar/en tab pairs when
rendering.

FIELDSETS carries an explicit annotation, unlike sibling change_view modules:
TabbedTranslationAdmin is a TYPED base (unfold's is Any), so this admin is the
first whose ``fieldsets`` assignment django-stubs actually checks - the
annotation gives the dict literals their TypedDict context.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.admin.options import _FieldsetSpec

FIELDSETS: _FieldsetSpec = (
    (None, {"fields": ("kind", "channels")}),
    ("Message", {"fields": ("title", "body")}),
)
READONLY_FIELDS = ()
