"""The unified API error contract.

Every error the API returns has ONE shape:

    {"message": "<human-readable>", "extra": {...}}

Field-level problems live under extra["fields"]. Ninja's 401/403/429 errors
all subclass ninja.errors.HttpError, so one handler covers them. The generic
Exception handler is deliberately NOT overridden: ninja re-raises when
DEBUG=False so Django (and Sentry's got_request_exception hook) own real 500s.
"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.http import HttpRequest
from django.http import HttpResponse
from django_ratelimit.exceptions import Ratelimited
from ninja import NinjaAPI
from ninja.errors import HttpError
from ninja.errors import ValidationError as RequestValidationError

from apps.common.exceptions import ApplicationError


def register_exception_handlers(api: NinjaAPI) -> None:
    """Attach the {"message", "extra"} envelope to every handled error type."""

    def respond(
        request: HttpRequest,
        *,
        message: str,
        status: int,
        extra: dict[str, Any] | None = None,
    ) -> HttpResponse:
        return api.create_response(
            request,
            {"message": message, "extra": extra or {}},
            status=status,
        )

    @api.exception_handler(ApplicationError)
    def application_error(request: HttpRequest, exc: ApplicationError) -> HttpResponse:
        return respond(
            request, message=exc.message, status=exc.status_code, extra=exc.extra
        )

    @api.exception_handler(DjangoValidationError)
    def model_validation_error(
        request: HttpRequest, exc: DjangoValidationError
    ) -> HttpResponse:
        # Raised by services' full_clean(); error_dict exists for field errors.
        if hasattr(exc, "error_dict"):
            fields: dict[str, Any] = exc.message_dict
        else:
            fields = {"non_field_errors": exc.messages}
        return respond(
            request, message="Validation error.", status=400, extra={"fields": fields}
        )

    @api.exception_handler(RequestValidationError)
    def request_validation_error(
        request: HttpRequest, exc: RequestValidationError
    ) -> HttpResponse:
        fields = {
            ".".join(str(part) for part in error.get("loc", ()))
            or "non_field_errors": error.get("msg", "Invalid value.")
            for error in exc.errors
        }
        return respond(
            request, message="Validation error.", status=422, extra={"fields": fields}
        )

    @api.exception_handler(HttpError)
    def http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
        # Also covers AuthenticationError (401), AuthorizationError (403),
        # and Throttled (429) - they subclass HttpError.
        return respond(request, message=str(exc), status=exc.status_code)

    @api.exception_handler(Http404)
    def not_found(request: HttpRequest, exc: Http404) -> HttpResponse:
        return respond(request, message="Not found.", status=404)

    @api.exception_handler(Ratelimited)
    def ratelimited(request: HttpRequest, exc: Ratelimited) -> HttpResponse:
        # django-ratelimit raises a PermissionDenied subclass; without this
        # handler a burst would surface as a 403/500 instead of 429.
        return respond(request, message="Too many requests.", status=429)
