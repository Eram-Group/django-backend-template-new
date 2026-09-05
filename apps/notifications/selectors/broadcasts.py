"""Reads for broadcasts - the audience definition lives HERE, used by the
dispatcher (paging a saved broadcast) and the composer's live reach (counting
the values on an unsaved form)."""

from collections.abc import Iterable
from datetime import date
from typing import Any

from django.db.models import QuerySet

from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.users.models import User


def audience_queryset(
    *,
    language: str,
    require_device: bool,
    joined_after: date | None,
    joined_before: date | None,
    recipient_ids: Iterable[Any],
) -> QuerySet[User]:
    """The ONE audience query. Hand-picked users win over the filters (still
    active, ``require_device`` still applies); otherwise the filters decide."""
    queryset = User.objects.filter(is_active=True)
    if require_device:
        # A subquery, not `devices__isnull=False`: the join multiplies a user
        # by their device count, and the dispatcher pages this queryset with a
        # pk cursor - duplicates there would send twice. `.distinct()` would
        # also dedupe but costs a sort on every page.
        queryset = queryset.filter(pk__in=Device.objects.values("user_id"))
    picked = list(recipient_ids)
    if picked:
        return queryset.filter(pk__in=picked)
    if language:
        queryset = queryset.filter(language=language)
    if joined_after:
        queryset = queryset.filter(created_at__date__gte=joined_after)
    if joined_before:
        queryset = queryset.filter(created_at__date__lte=joined_before)
    return queryset


def broadcast_audience(*, broadcast: Broadcast) -> QuerySet[User]:
    return audience_queryset(
        language=broadcast.language,
        require_device=broadcast.require_device,
        joined_after=broadcast.joined_after,
        joined_before=broadcast.joined_before,
        recipient_ids=broadcast.recipients.values_list("pk", flat=True),
    )


def audience_summary(*, audience: QuerySet[User]) -> dict[str, int]:
    """Recipient and reachable-device counts for an audience queryset."""
    return {
        "recipients": audience.count(),
        "devices": Device.objects.filter(user_id__in=audience.values("pk")).count(),
    }


def broadcast_audience_summary(*, broadcast: Broadcast) -> dict[str, int]:
    """Counts for a saved broadcast (the change form's Reach field, the
    dispatch guard) - produced by the same query the dispatcher pages."""
    return audience_summary(audience=broadcast_audience(broadcast=broadcast))
