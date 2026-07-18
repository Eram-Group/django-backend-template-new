"""Typing helpers for django-ninja request objects."""

from django.http import HttpRequest


class AuthedRequest[UserT](HttpRequest):
    """Typing-only: ninja sets ``request.auth`` to the authenticated user.

    Generic so apps.common stays free of domain imports - routers annotate
    handlers with ``AuthedRequest[User]``.
    """

    auth: UserT
