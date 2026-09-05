"""Sidebar badge: how many payments need a human (UNFOLD["SIDEBAR"])."""

from django.http import HttpRequest

from apps.payments import selectors


def payments_needing_attention(request: HttpRequest) -> str:
    """Runs once per admin page render (one COUNT); "" hides the badge."""
    if not request.user.has_perm("payments.view_payment"):
        return ""
    count = selectors.payments_needing_attention().count()
    return str(count) if count else ""
