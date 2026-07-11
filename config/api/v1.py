"""API v1 root - assembled from per-app routers, served at /api/v1/.

Docs are open in local and staff-gated everywhere else.
"""

from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI

from config.api.exception_handlers import register_exception_handlers
from config.env import env

api = NinjaAPI(
    title="Backend API",
    version="1",
    urls_namespace="api-v1",
    docs_decorator=None if env.ENVIRONMENT == "local" else staff_member_required,
)
register_exception_handlers(api)

# Per-app routers are mounted by urls-entrypoints (G03), e.g.:
#   from apps.users.apis import router as users_router
#   api.add_router("/users", users_router)
