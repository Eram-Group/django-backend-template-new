"""The users app's public router - mounted by config/api/v1.py at /users/ (G03)."""

from ninja import Router

from apps.users.apis.users import router as users_router

router = Router()
router.add_router("", users_router)
