from ninja import Router
from ninja.responses import Status

from apps.common.requests import AuthedRequest
from apps.notifications import services
from apps.notifications.models import Device
from apps.notifications.schemas import DeviceDetail
from apps.notifications.schemas import DeviceRegisterIn
from apps.users.models import User

router = Router(tags=["notifications"])


@router.post("/devices", response=DeviceDetail, summary="Register this device")
def device_register(request: AuthedRequest[User], payload: DeviceRegisterIn) -> Device:
    """Idempotent upsert on the FCM registration token; call on every app
    launch and after token rotation."""
    return services.device_register(
        user=request.auth,
        registration_id=payload.registration_id,
        platform=payload.platform,
    )


@router.delete(
    "/devices/{registration_id}",
    response={204: None},
    summary="Unregister this device",
)
def device_unregister(
    request: AuthedRequest[User], registration_id: str
) -> Status[None]:
    """Call on logout so the signed-out device stops receiving pushes."""
    services.device_unregister(user=request.auth, registration_id=registration_id)
    return Status(204, None)
