"""Capability + field decisions for the Notification admin.

Rows are created by services (notification_send) and only read/pruned here -
operators never author or edit notifications by hand, so the change form
stays a read-only inspection view.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = True  # inbox pruning / cleanup

FIELD_PERMISSIONS = FieldPermissions()
