"""Explicit capability + field decisions for the User admin."""

from apps.common.admin import FieldPermissions
from apps.common.admin import on_change

CAN_ADD = False  # users exist only via signup; superuser via `just superuser`
CAN_CHANGE = True
CAN_DELETE = True

FIELD_PERMISSIONS = FieldPermissions(
    # The login identity: editing it in admin desyncs allauth's EmailAddress.
    readonly_when={"email": on_change},
)
