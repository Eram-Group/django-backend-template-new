from apps.common.exceptions import ApplicationError


class LocationError(ApplicationError):
    """Base error for the location domain."""


class CountryNotFoundError(LocationError):
    status_code = 404


class ZoneNotFoundError(LocationError):
    status_code = 404


class ZoneFileError(LocationError):
    """The uploaded GeoJSON cannot become zones (shape, country, geometry)."""


class InvalidCoordinatesError(LocationError):
    """A lookup point outside WGS84 bounds."""
