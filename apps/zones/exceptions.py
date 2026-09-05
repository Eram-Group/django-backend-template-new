from apps.common.exceptions import ApplicationError


class ZonesError(ApplicationError):
    """Base error for the zones domain."""


class ZoneFileError(ZonesError):
    """The uploaded GeoJSON cannot become zones (shape, country, geometry)."""


class InvalidCoordinatesError(ZonesError):
    """A lookup point outside WGS84 bounds."""
