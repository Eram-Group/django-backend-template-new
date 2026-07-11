"""Default API authentication - two credential types, one request.auth contract.

Browser clients authenticate with the Django session cookie (+ CSRF on
unsafe methods); mobile/app clients send the allauth headless
X-Session-Token. Either way request.auth is the authenticated User;
failure raises ninja's AuthenticationError -> the unified 401 envelope.

allauth exposes token resolution via headless.internal.sessionkit - an
internal module, pinned by the test suite (G07) so upgrades that move it
fail loudly.
"""

from allauth.headless.internal.sessionkit import authenticate_by_x_session_token
from django.contrib.auth.base_user import AbstractBaseUser
from django.http import HttpRequest
from ninja.security import APIKeyHeader
from ninja.security import django_auth


class SessionTokenAuth(APIKeyHeader):
    """allauth headless app-client tokens (X-Session-Token header)."""

    param_name = "X-Session-Token"

    def authenticate(
        self, request: HttpRequest, key: str | None
    ) -> AbstractBaseUser | None:
        if not key:
            return None
        result = authenticate_by_x_session_token(key)
        if result is None:
            return None
        user: AbstractBaseUser = result[0]  # (user, session) tuple, untyped upstream
        return user


# Tried in order: session cookie (browser SPA), then app-client token.
api_auth = [django_auth, SessionTokenAuth()]
