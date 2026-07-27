"""The notifications app's public router - mounted at /notifications/."""

from ninja import Router

from apps.notifications.apis.devices import router as devices_router
from apps.notifications.apis.notifications import router as notifications_router
from apps.notifications.apis.webhooks import router as webhooks_router

router = Router()
router.add_router("", notifications_router)
router.add_router("", devices_router)
router.add_router("", webhooks_router)
