"""Thin /users routers - no business logic here.

Authentication is the NinjaAPI default auth (session cookie or
X-Session-Token, G04 ninja-auth-class); request.auth is the User.
"""

from ninja import PatchDict
from ninja import Router
from ninja.responses import Status

from apps.common.requests import AuthedRequest
from apps.users import services
from apps.users.models import User
from apps.users.schemas import UserDetail
from apps.users.schemas import UserUpdateIn

router = Router(tags=["users"])


@router.get("/me", response=UserDetail, summary="Current user")
def user_me(request: AuthedRequest[User]) -> User:
    return request.auth


@router.patch("/me", response=UserDetail, summary="Update current user")
def user_me_update(
    request: AuthedRequest[User], payload: PatchDict[UserUpdateIn]
) -> User:
    return services.user_update(user=request.auth, data=payload)


@router.delete("/me", response={204: None}, summary="Deactivate current user")
def user_me_deactivate(request: AuthedRequest[User]) -> Status[None]:
    services.user_deactivate(user=request.auth)
    return Status(204, None)
