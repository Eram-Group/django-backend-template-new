"""URL configuration.

The users router mounts into the API alongside the ninja auth class (G04);
allauth headless endpoints mount with the headless settings (G04).
"""

from django.conf import settings
from django.contrib import admin
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import JsonResponse
from django.urls import URLPattern
from django.urls import URLResolver
from django.urls import include
from django.urls import path
from django.views.defaults import page_not_found
from django.views.defaults import server_error

from config.api.v1 import api
from config.env import env


def handle_404(
    request: HttpRequest, exception: Exception | None = None
) -> HttpResponse:
    """Unmatched API paths keep the JSON error contract; the rest stay HTML."""
    if request.path.startswith("/api/"):
        return JsonResponse({"message": "Not found.", "extra": {}}, status=404)
    return page_not_found(request, exception)


def handle_500(request: HttpRequest) -> HttpResponse:
    """Crashed API requests keep the JSON error contract; the rest stay HTML.

    Runs after Django's exception logging/Sentry capture - this only shapes
    the response body, never swallows the error.
    """
    if request.path.startswith("/api/"):
        return JsonResponse(
            {"message": "Something went wrong on our side.", "extra": {}},
            status=500,
        )
    return server_error(request)


handler404 = "config.urls.handle_404"
handler500 = "config.urls.handle_500"

# /healthz and /readyz are answered by apps.common.middleware.HealthProbeMiddleware
# before any URL resolution (ALB probes bypass host + TLS checks).
urlpatterns: list[URLPattern | URLResolver] = [
    path(env.ADMIN_URL, admin.site.urls),
    path("api/v1/", api.urls),
    path("_allauth/", include("allauth.headless.urls")),
    # set_language for the admin's ar/en switcher (UNFOLD SHOW_LANGUAGES).
    path("i18n/", include("django.conf.urls.i18n")),
]

if settings.DEBUG:
    from debug_toolbar.toolbar import debug_toolbar_urls
    from django.conf.urls.static import static

    urlpatterns += debug_toolbar_urls()
    # runserver serves static via the staticfiles app but never media -
    # without this, locally uploaded files 404 on retrieval.
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
