"""Reads for broadcasts - the audience definition lives HERE, used by both
the dispatcher (paging) and the inline-backend guard (counting)."""

from django.db.models import QuerySet

from apps.notifications.models import Broadcast
from apps.users.models import User


def broadcast_audience(*, broadcast: Broadcast) -> QuerySet[User]:
    queryset = User.objects.filter(is_active=True)
    if broadcast.language:
        queryset = queryset.filter(language=broadcast.language)
    return queryset
