"""Capability + field decisions for the NotificationKindConfig admin.

THE operator surface for "this action sends on these channels, saying this".
Rows are born in migration 0004, exactly one per kind - operators edit
channels and copy, never the row set itself (a missing row fails sends
loudly, so add/delete stay off).
"""

from apps.common.admin import AdminContext
from apps.common.admin import FieldPermissions
from apps.common.admin import always
from apps.notifications.constants import NotificationKind

CAN_ADD = False  # one row per kind, born in migration 0004
CAN_CHANGE = True
CAN_DELETE = False  # a deleted row = LookupError on the next send


def message_locked(context: AdminContext) -> bool:
    """authored_per_send kinds (the broadcast composer's ANNOUNCEMENT) keep
    their passthrough title/body - the message is written per broadcast."""
    from apps.notifications.catalog import catalog_entry

    kind = getattr(context.obj, "kind", None)
    if kind is None:
        return False
    try:
        entry = catalog_entry(NotificationKind(kind))
    except ValueError, LookupError:
        return False
    return entry.authored_per_send


FIELD_PERMISSIONS = FieldPermissions(
    readonly_when={
        "kind": always,  # the identity of the row, never retyped
        # Rules auto-cover the modeltranslation _ar/_en shadow columns.
        "title": message_locked,
        "body": message_locked,
    },
)
