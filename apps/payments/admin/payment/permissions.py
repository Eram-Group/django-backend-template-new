"""Capability + field decisions for the Payment admin.

Rows are created by payment_initiate and transitioned by gateway events -
the admin inspects; the ONLY write is the Refund detail action (which calls
the service). Nothing here is ever hand-edited or deleted.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = False  # financial history is append-only

FIELD_PERMISSIONS = FieldPermissions()
