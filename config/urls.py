"""URL configuration.

The users router mounts into the API alongside the ninja auth class (G04);
allauth headless endpoints mount with the headless settings (G04).
"""

from django.conf import settings
from django.contrib import admin
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.urls import path
from django.views.defaults import page_not_found

from apps.common.health import healthz
from apps.common.health import readyz
from config.api.v1 import api
from config.env import env


def handle_404(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    """Unmatched API paths keep the JSON error contract; the rest stay HTML."""
    if request.path.startswith("/api/"):
        return JsonResponse({"message": "Not found.", "extra": {}}, status=404)
    return page_not_found(request, exception)


handler404 = "config.urls.handle_404"

urlpatterns = [
    path(env.ADMIN_URL, admin.site.urls),
    path("api/v1/", api.urls),
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
]

if settings.DEBUG:
    from debug_toolbar.toolbar import debug_toolbar_urls

    urlpatterns += debug_toolbar_urls()
