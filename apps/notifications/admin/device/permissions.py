"""Capability + field decisions for the Device admin.

Rows are managed by the device_register/device_unregister services (and by
FCM invalid-token pruning) - the admin only inspects and force-deletes.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = True  # force-detach a leaked/stolen device token

FIELD_PERMISSIONS = FieldPermissions()
