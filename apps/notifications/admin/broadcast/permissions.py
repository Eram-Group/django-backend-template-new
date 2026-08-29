"""Capability + field decisions for the Broadcast admin.

Operators author a DRAFT here (kind, context, audience), then move it
through its lifecycle with the Dispatch/Resume detail actions - status,
cursor, and counters are code-owned and stay read-only; content freezes
once the row exists (a dispatched broadcast must show what was sent).
"""

from apps.common.admin import FieldPermissions
from apps.common.admin import on_change

CAN_ADD = True
CAN_CHANGE = True  # the change view hosts the lifecycle actions
CAN_DELETE = False  # PROTECT semantics: history of what was sent stays

FIELD_PERMISSIONS = FieldPermissions(
    readonly_when={
        "kind": on_change,
        "context": on_change,
        "language": on_change,
        # Audience and channels freeze with the content: a dispatched
        # broadcast must keep showing who it actually went to and how.
        "require_device": on_change,
        "joined_after": on_change,
        "joined_before": on_change,
        "channels": on_change,
    },
)
