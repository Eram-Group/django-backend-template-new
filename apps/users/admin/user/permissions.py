"""Explicit capability + field decisions for the User admin."""

from apps.common.admin import AdminContext
from apps.common.admin import FieldPermissions
from apps.common.admin import on_change

CAN_ADD = False  # users exist only via signup; superuser via `just superuser`
CAN_CHANGE = True
# Account removal is deactivation (users.user_deactivate): payments, wallets
# and saved cards PROTECT their user FK, so a row delete of anyone who ever
# paid raised ProtectedError in the admin.
CAN_DELETE = False


def _not_superuser(context: AdminContext) -> bool:
    return not context.is_superuser


FIELD_PERMISSIONS = FieldPermissions(
    readonly_when={
        # The login identity: editing it in admin desyncs allauth's EmailAddress.
        "email": on_change,
        # Privilege surface: staff membership and group (= permission) grants
        # are superuser-only decisions.
        "is_staff": _not_superuser,
        "groups": _not_superuser,
    },
    hidden_when={
        # Non-superusers never see - or POST - the superuser bit.
        "is_superuser": _not_superuser,
    },
)
