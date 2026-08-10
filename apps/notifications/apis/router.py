from ninja import Router

from apps.notifications.apis.devices import router as devices_router
from apps.notifications.apis.notifications import router as notifications_router

router = Router()
router.add_router("", notifications_router)
router.add_router("", devices_router)
