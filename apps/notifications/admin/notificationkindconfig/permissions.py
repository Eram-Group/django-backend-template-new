"""Capability + field decisions for the NotificationKindConfig admin.

THE operator surface for "this action sends on these channels, saying this".
Rows are born in migration 0005, exactly one per kind - operators edit
channels and copy, never the row set itself (a missing row fails sends
loudly, so add/delete stay off).
"""

from typing import cast

from apps.common.admin import AdminContext
from apps.common.admin import FieldPermissions
from apps.notifications.constants import NotificationKind

CAN_ADD = False  # one row per kind, born in migration 0005
CAN_CHANGE = True
CAN_DELETE = False  # a deleted row = LookupError on the next send


def message_locked(context: AdminContext) -> bool:
    """authored_per_send kinds (the broadcast composer's ANNOUNCEMENT) keep
    their passthrough title/body - the message is written per broadcast.

    Change view only (CAN_ADD is False, so ``context.obj`` is always a row),
    and every row's kind is a catalog kind - the field is read-only and the
    rows were born from the catalog.
    """
    from apps.notifications.catalog import catalog_entry
    from apps.notifications.models import NotificationKindConfig

    config = cast("NotificationKindConfig", context.obj)
    return catalog_entry(NotificationKind(config.kind)).authored_per_send


FIELD_PERMISSIONS = FieldPermissions(
    readonly_when={
        # Rules auto-cover the modeltranslation _ar/_en shadow columns.
        "title": message_locked,
        "body": message_locked,
    },
)
