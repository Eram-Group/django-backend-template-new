"""API v1 root - assembled from per-app routers, served at /api/v1/.

Docs are staff-gated (log into the admin first).
"""

from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI

from config.api.auth import api_auth
from config.api.exception_handlers import register_exception_handlers

api = NinjaAPI(
    title="Backend API",
    version="1",
    urls_namespace="api-v1",
    auth=api_auth,
    docs_decorator=staff_member_required,
)
register_exception_handlers(api)

# Per-app routers - one line per app.
api.add_router("/users", "apps.users.apis.router.router")
api.add_router("/notifications", "apps.notifications.apis.router.router")
api.add_router("/payments", "apps.payments.apis.router.router")
