"""Channel-policy resolution: catalog defaults + admin override pins.

Per supported channel: an override row wins, otherwise the catalog default
decides. A plain SELECT on a tiny table, resolved once per send / per
broadcast dispatch - no cache, so a live admin edit is authoritative
immediately.
"""

from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationChannelOverride


def effective_channels(*, kind: NotificationKind) -> frozenset[Channel]:
    entry = catalog_entry(kind)
    pins: dict[str, bool] = dict(
        NotificationChannelOverride.objects.filter(kind=kind).values_list(
            "channel", "enabled"
        )
    )
    return frozenset(
        channel
        for channel in entry.supported_channels
        if pins.get(channel, channel in entry.default_channels)
    )
