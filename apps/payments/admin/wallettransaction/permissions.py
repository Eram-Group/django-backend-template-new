"""Capability + field decisions for the WalletTransaction admin.

The ledger is append-only BY SERVICES - the admin never adds, edits, or
deletes rows (an admin-added row would move no balance and corrupt the
balance_after chain).
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = False

FIELD_PERMISSIONS = FieldPermissions()
