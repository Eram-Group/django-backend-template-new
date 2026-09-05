"""GeoJSON FeatureCollection -> zone features (the load sheet's parser).

The source files are one FeatureCollection per region group, every feature
carrying ``properties.{name_en, name_ar, country_code, zone_code,
region_code}`` and a Polygon or MultiPolygon. This module turns that shape
into validated ``ZoneFeature`` rows for ONE country and nothing else: no
database access, so the service can stay a plain upsert loop.

Every rejection is a ZoneFileError naming the feature (1-based) so the
operator can fix the file; a file is accepted whole or not at all.
"""

import json
from dataclasses import dataclass
from typing import Any
from typing import cast

from django.contrib.gis.gdal.error import GDALException
from django.contrib.gis.geos import GEOSGeometry
from django.contrib.gis.geos import MultiPolygon
from django.contrib.gis.geos.error import GEOSException
from django.utils.text import slugify
from django.utils.translation import gettext as _

from apps.location.models import Country
from apps.zones.exceptions import ZoneFileError

MAX_DOCUMENT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ZoneFeature:
    code: str
    region_code: str
    zone_code: str
    name_en: str
    name_ar: str
    geometry: MultiPolygon
    # False when the file left a name empty: the row is created inactive
    # with the code as its name, for an operator to finish.
    named: bool


def zone_features(*, document: bytes, country: Country) -> list[ZoneFeature]:
    """Parse ``document`` into the zones of ``country``, in file order."""
    features = _feature_list(document)
    accepted_countries = {country.code.upper(), country.alpha_3.upper()}
    result: list[ZoneFeature] = []
    seen: dict[str, int] = {}
    for index, feature in enumerate(features, start=1):
        if not isinstance(feature, dict):
            raise ZoneFileError(
                _("Feature %(index)d is not an object.") % {"index": index}
            )
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise ZoneFileError(
                _("Feature %(index)d has no properties.") % {"index": index}
            )
        country_code = _text(properties.get("country_code")).upper()
        if country_code not in accepted_countries:
            raise ZoneFileError(
                _(
                    "Feature %(index)d belongs to %(found)s, not %(country)s "
                    "(%(code)s/%(alpha_3)s)."
                )
                % {
                    "index": index,
                    "found": country_code or "-",
                    "country": country.name_en,
                    "code": country.code,
                    "alpha_3": country.alpha_3,
                }
            )
        region_code = _text(properties.get("region_code")).upper()
        zone_code = _text(properties.get("zone_code"))
        if not region_code or not zone_code:
            raise ZoneFileError(
                _("Feature %(index)d is missing region_code or zone_code.")
                % {"index": index}
            )
        code = slugify(f"{country.code}-{region_code}-{zone_code}")
        if code in seen:
            raise ZoneFileError(
                _("Features %(first)d and %(index)d both map to code %(code)s.")
                % {"first": seen[code], "index": index, "code": code}
            )
        seen[code] = index
        name_en = _text(properties.get("name_en"))
        name_ar = _text(properties.get("name_ar"))
        named = bool(name_en and name_ar)
        result.append(
            ZoneFeature(
                code=code,
                region_code=region_code,
                zone_code=zone_code,
                name_en=name_en or code,
                name_ar=name_ar or code,
                geometry=_multipolygon(feature.get("geometry"), index=index),
                named=named,
            )
        )
    return result


def _feature_list(document: bytes) -> list[Any]:
    if len(document) > MAX_DOCUMENT_BYTES:
        raise ZoneFileError(
            _("The file is larger than %(limit)d MB.")
            % {"limit": MAX_DOCUMENT_BYTES // (1024 * 1024)}
        )
    try:
        data = json.loads(document.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ZoneFileError(_("The file must be UTF-8 text.")) from exc
    except json.JSONDecodeError as exc:
        raise ZoneFileError(_("The file is not valid JSON.")) from exc
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
        raise ZoneFileError(_("The file must be a GeoJSON FeatureCollection."))
    features = data.get("features")
    if not isinstance(features, list) or not features:
        raise ZoneFileError(_("The FeatureCollection has no features."))
    return features


def _text(value: object) -> str:
    """Property text, normalised: NBSP -> space, whitespace collapsed."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())


def _multipolygon(geometry: object, *, index: int) -> MultiPolygon:
    if not isinstance(geometry, dict):
        raise ZoneFileError(_("Feature %(index)d has no geometry.") % {"index": index})
    kind = geometry.get("type")
    if kind not in ("Polygon", "MultiPolygon"):
        raise ZoneFileError(
            _(
                "Feature %(index)d: only Polygon and MultiPolygon geometries "
                "are accepted, got %(type)s."
            )
            % {"index": index, "type": kind or "-"}
        )
    # Drop any third coordinate before parsing: the column is 2D and GEOS
    # would otherwise carry Z into PostGIS and fail the dimension check.
    flat = {"type": kind, "coordinates": _two_d(geometry.get("coordinates"))}
    try:
        geom = GEOSGeometry(json.dumps(flat), srid=4326)
    except (GEOSException, GDALException, ValueError, TypeError) as exc:
        raise ZoneFileError(
            _("Feature %(index)d has an unreadable geometry.") % {"index": index}
        ) from exc
    if geom.empty:
        raise ZoneFileError(
            _("Feature %(index)d has an empty geometry.") % {"index": index}
        )
    if not geom.valid:
        raise ZoneFileError(
            _("Feature %(index)d has an invalid geometry: %(reason)s")
            % {"index": index, "reason": geom.valid_reason}
        )
    if kind == "Polygon":
        return MultiPolygon(geom, srid=4326)
    return cast("MultiPolygon", geom)  # kind is "MultiPolygon" past the check above


def _two_d(coordinates: object) -> object:
    """Recursively keep only [x, y] of every position."""
    if isinstance(coordinates, list) and coordinates:
        if isinstance(coordinates[0], int | float):
            return coordinates[:2]
        return [_two_d(part) for part in coordinates]
    return coordinates
