"""Capability + field decisions for the NotificationChannelOverride admin.

THE operator surface for "this action sends on these channels": pin a
channel on/off per kind at runtime, no deploy. ``clean()`` refuses channels
the catalog does not support for the kind.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = True
CAN_CHANGE = True
CAN_DELETE = True  # deleting a pin falls back to the catalog default

FIELD_PERMISSIONS = FieldPermissions()
