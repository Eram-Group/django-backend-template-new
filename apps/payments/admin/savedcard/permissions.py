"""Capability + field decisions for the SavedCard admin.

Rows are created BY WEBHOOKS and deleted through saved_card_delete (which
also detaches the token at the gateway) - an admin add would invent a token
the provider never issued, and an admin hard-delete would skip the
gateway-side detach. Support removes cards via the API/service path.
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = False

FIELD_PERMISSIONS = FieldPermissions()
