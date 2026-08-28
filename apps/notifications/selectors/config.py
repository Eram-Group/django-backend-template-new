"""Channel-policy resolution: the kind's config row, or a per-broadcast pick.

Each kind's NotificationKindConfig row lists its channels explicitly (empty =
inbox-only) - there is no default layer behind it. A plain SELECT on a tiny
table, resolved once per send / per broadcast dispatch page - no cache, so a
live admin edit is authoritative immediately.
"""

from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.selectors.messages import notification_config_get


def effective_channels(
    *, kind: NotificationKind, broadcast: Broadcast | None = None
) -> frozenset[Channel]:
    entry = catalog_entry(kind)
    if broadcast is not None and broadcast.channels:
        # A per-broadcast pick overrides the kind's policy for this send only.
        selected = broadcast.channels
    else:
        selected = notification_config_get(kind=kind).channels
    # Intersected with the catalog: a row written before a channel was
    # withdrawn from the kind must not resurrect it. A value unknown to the
    # Channel enum itself still fails loudly (enum shrink = code change that
    # owes a data migration).
    return frozenset(Channel(channel) for channel in selected) & frozenset(
        entry.supported_channels
    )
