"""Sidebar badge: how many deliveries need a human (UNFOLD["SIDEBAR"])."""

from django.http import HttpRequest

from apps.notifications import selectors


def deliveries_needing_attention(request: HttpRequest) -> str:
    """Runs once per admin page render (one COUNT); "" hides the badge."""
    if not request.user.has_perm("notifications.view_notificationdelivery"):
        return ""
    count = selectors.deliveries_needing_attention().count()
    return str(count) if count else ""
