"""API v1 root - assembled from per-app routers, served at /api/v1/.

Docs are staff-gated (log into the admin first).

Throttling: one API-wide ceiling per authenticated principal, counted in the
shared DatabaseCache (global across web tasks); an endpoint that deserves a
tighter limit declares its own ``throttle=`` (payments checkout does), and
the signature-verified webhooks opt out with ``throttle=[]`` (a gateway
retry storm from one IP must never be dropped). ninja checks throttles
after auth, so unauthenticated calls stop at 401 first. Bursts surface as
the 429 envelope through the HttpError handler. allauth's endpoints carry
their own per-ip/per-key limits (``ACCOUNT_RATE_LIMITS`` defaults, e.g.
login-code requests 20/m per ip).
"""

from django.contrib.admin.views.decorators import staff_member_required
from ninja import NinjaAPI

from apps.common.throttling import PrincipalRateThrottle
from config.api.auth import api_auth
from config.api.exception_handlers import register_exception_handlers

api = NinjaAPI(
    title="Backend API",
    version="1",
    urls_namespace="api-v1",
    auth=api_auth,
    throttle=[PrincipalRateThrottle("600/m", scope="api")],
    docs_decorator=staff_member_required,
)
register_exception_handlers(api)

# Per-app routers - one line per app.
api.add_router("/users", "apps.users.apis.router.router")
api.add_router("/notifications", "apps.notifications.apis.router.router")
api.add_router("/payments", "apps.payments.apis.router.router")
