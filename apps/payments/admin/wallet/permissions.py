"""Capability + field decisions for the Wallet admin.

Balance moves ONLY through services.wallet_apply (row lock + ledger entry);
an editable admin field would bypass the ledger. Manual adjustments:
`manage.py shell` -> wallet_apply(kind=ADJUSTMENT, actor=..., note=...).
"""

from apps.common.admin import FieldPermissions

CAN_ADD = False
CAN_CHANGE = False
CAN_DELETE = False

FIELD_PERMISSIONS = FieldPermissions()
