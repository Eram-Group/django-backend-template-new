"""Capability + field decisions for the NotificationDelivery admin.

Rows are written by the delivery executor and status webhooks - the admin
inspects outcomes (and prunes) but never authors or edits them; resume goes
through the Broadcast actions / sweep_deliveries, not row edits.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = True  # log pruning / cleanup

FIELD_PERMISSIONS = FieldPermissions()
