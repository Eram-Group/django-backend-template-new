"""Capability + field decisions for the Country admin.

Rows are born from the load sheet (ISO data), so the generic add form is
off; deletion is off too - deactivate instead, future FKs PROTECT the row.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False  # the "Load countries" sheet is the only creation road
CAN_CHANGE = True  # names, flag, is_active
CAN_DELETE = False  # deactivate; a deleted market would orphan future FKs

# ISO-derived columns are unconditionally readonly (change_view.READONLY_FIELDS).
FIELD_PERMISSIONS = FieldPermissions()
