"""Thin /users routers - no business logic here.

Authentication is the NinjaAPI default auth (session cookie or
X-Session-Token, G04 ninja-auth-class); request.auth is the User.
"""

from django.http import HttpRequest
from ninja import Router

from apps.users import selectors
from apps.users import services
from apps.users.models import User
from apps.users.schemas import UserDetail
from apps.users.schemas import UserUpdateIn


class AuthedRequest(HttpRequest):
    """Typing-only: ninja sets request.auth to the authenticated User."""

    auth: User


router = Router(tags=["users"])


@router.get("/me", response=UserDetail, summary="Current user")
def user_me(request: AuthedRequest) -> User:
    return selectors.user_get(pk=request.auth.pk)


@router.patch("/me", response=UserDetail, summary="Update current user")
def user_me_update(request: AuthedRequest, payload: UserUpdateIn) -> User:
    return services.user_update(
        user=request.auth,
        data=payload.dict(exclude_unset=True),
    )
