"""The location app's public router - mounted at /location/."""

from ninja import Router

from apps.location.apis.countries import router as countries_router

router = Router()
router.add_router("", countries_router)
