"""Channel resolution: a broadcast's own pick, or the kind's config row.

A broadcast always carries the channels it goes out on (the composer requires
them - no kind-level default behind it). Every other send reads its kind's
NotificationKindConfig row, which lists channels explicitly (empty =
inbox-only); a kind without a row is inbox-only too. A plain SELECT on a tiny
table, resolved once per send / per broadcast dispatch page - no cache, so a
live admin edit is authoritative immediately.
"""

from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import NotificationKindConfig


def effective_channels(
    *, kind: NotificationKind, broadcast: Broadcast | None = None
) -> frozenset[Channel]:
    entry = catalog_entry(kind)
    if broadcast is not None:
        selected = broadcast.channels
    else:
        config = NotificationKindConfig.objects.filter(kind=kind).first()
        selected = config.channels if config is not None else []
    # Intersected with the catalog: a row written before a channel was
    # withdrawn from the kind must not resurrect it. A value unknown to the
    # Channel enum itself still fails loudly (enum shrink = code change that
    # owes a data migration).
    return frozenset(Channel(channel) for channel in selected) & frozenset(
        entry.supported_channels
    )
