"""Capability + field decisions for the Zone admin.

Rows are born from the load sheet (GeoJSON files), so the generic add form
is off. Operators edit names, region and is_active; identity (code, country)
and the geometry are readonly (change_view.READONLY_FIELDS) and come back
from a re-load. Deletion is on: nothing references a zone yet.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False  # the "Load zones" sheet is the only creation road
CAN_CHANGE = True  # names, region_code, is_active
CAN_DELETE = True

FIELD_PERMISSIONS = FieldPermissions()
