"""Reads for broadcasts - the audience definition lives HERE, used by both
the dispatcher (paging) and the change form's reach estimate (counting)."""

from django.db.models import QuerySet

from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.users.models import User


def broadcast_audience(*, broadcast: Broadcast) -> QuerySet[User]:
    queryset = User.objects.filter(is_active=True)
    if broadcast.require_device:
        # A subquery, not `devices__isnull=False`: the join multiplies a user
        # by their device count, and the dispatcher pages this queryset with a
        # pk cursor - duplicates there would send twice. `.distinct()` would
        # also dedupe but costs a sort on every 5k page.
        queryset = queryset.filter(pk__in=Device.objects.values("user_id"))
    if broadcast.recipients.exists():
        # Hand-picked users: exactly these (minus anyone since deactivated).
        # The language/date filters do not apply; require_device still does.
        return queryset.filter(pk__in=broadcast.recipients.values("pk"))
    if broadcast.language:
        queryset = queryset.filter(language=broadcast.language)
    if broadcast.joined_after:
        queryset = queryset.filter(created_at__date__gte=broadcast.joined_after)
    if broadcast.joined_before:
        queryset = queryset.filter(created_at__date__lte=broadcast.joined_before)
    return queryset


def broadcast_audience_summary(*, broadcast: Broadcast) -> dict[str, int]:
    """Recipient and reachable-device counts for a saved broadcast (the
    change form's Reach field, the dispatch guard) - produced by the same
    query the dispatcher pages, not a parallel reimplementation."""
    audience = broadcast_audience(broadcast=broadcast)
    return {
        "recipients": audience.count(),
        "devices": Device.objects.filter(user_id__in=audience.values("pk")).count(),
    }
