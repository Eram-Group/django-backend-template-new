from apps.common.exceptions import ApplicationError


class LocationError(ApplicationError):
    """Base error for the location domain."""


class CountryNotFoundError(LocationError):
    status_code = 404
