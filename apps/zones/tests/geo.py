"""GeoJSON builders shared by the zone tests."""

import json
from typing import Any

SQUARE = [[46.6, 24.6], [46.8, 24.6], [46.8, 24.8], [46.6, 24.8], [46.6, 24.6]]
# A square with a hole in the middle.
HOLE = [[46.68, 24.68], [46.72, 24.68], [46.72, 24.72], [46.68, 24.72], [46.68, 24.68]]
# Self-intersecting ring.
BOWTIE = [[46.6, 24.6], [46.8, 24.8], [46.8, 24.6], [46.6, 24.8], [46.6, 24.6]]


def feature(
    *,
    zone_code: object = 1,
    region_code: str = "RD",
    country_code: str = "SA",
    name_en: str = "Riyadh north",
    name_ar: str = "شمال الرياض",
    geometry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "OBJECTID": 1,
            "name_en": name_en,
            "name_ar": name_ar,
            "country_code": country_code,
            "zone_code": zone_code,
            "region_code": region_code,
        },
        "geometry": geometry or {"type": "Polygon", "coordinates": [SQUARE]},
    }


def collection(*features: dict[str, Any]) -> bytes:
    return json.dumps(
        {"type": "FeatureCollection", "features": list(features)}
    ).encode()
