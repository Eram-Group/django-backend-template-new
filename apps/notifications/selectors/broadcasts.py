"""Reads for broadcasts - the audience definition lives HERE, used by both
the dispatcher (paging) and the composer's reach estimate (counting)."""

from django.db.models import Q
from django.db.models import QuerySet

from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.users.models import User

USER_SEARCH_LIMIT = 20


def broadcast_audience(*, broadcast: Broadcast) -> QuerySet[User]:
    queryset = User.objects.filter(is_active=True)
    if broadcast.recipient_ids:
        # Hand-picked users: exactly these (minus anyone since deactivated).
        # The language/date filters do not apply; require_device still does.
        queryset = queryset.filter(pk__in=broadcast.recipient_ids)
    elif broadcast.language:
        queryset = queryset.filter(language=broadcast.language)
    if broadcast.require_device:
        # A subquery, not `devices__isnull=False`: the join multiplies a user
        # by their device count, and the dispatcher pages this queryset with a
        # pk cursor - duplicates there would send twice. `.distinct()` would
        # also dedupe but costs a sort on every 5k page.
        queryset = queryset.filter(pk__in=Device.objects.values("user_id"))
    if broadcast.recipient_ids:
        return queryset
    if broadcast.joined_after:
        queryset = queryset.filter(created_at__date__gte=broadcast.joined_after)
    if broadcast.joined_before:
        queryset = queryset.filter(created_at__date__lte=broadcast.joined_before)
    return queryset


def broadcast_user_search(*, query: str) -> QuerySet[User]:
    """Active users matching the composer's "specific users" search box -
    by name, email or phone, a short page for a picker (never the whole
    user base)."""
    text = query.strip()
    if not text:
        return User.objects.none()
    return (
        User.objects.filter(is_active=True)
        .filter(
            Q(name__icontains=text)
            | Q(email__icontains=text)
            | Q(phone__icontains=text)
        )
        .order_by("name", "email")[:USER_SEARCH_LIMIT]
    )


def broadcast_audience_summary(*, broadcast: Broadcast) -> dict[str, int]:
    """Recipient and reachable-device counts for the compose screen.

    Takes the Broadcast by attributes only, so an unsaved instance built from
    unsaved form values works - the estimate is then produced by the same query
    the dispatcher pages, not a parallel reimplementation that could drift.
    """
    audience = broadcast_audience(broadcast=broadcast)
    return {
        "recipients": audience.count(),
        "devices": Device.objects.filter(user_id__in=audience.values("pk")).count(),
    }
