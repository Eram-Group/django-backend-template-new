"""Writes for zones - the load road and the operator edits."""

from dataclasses import dataclass
from typing import Any

from django.db import transaction

from apps.location.models import Country
from apps.zones.geojson import zone_features
from apps.zones.models import Zone

ZONE_UPDATABLE_FIELDS = frozenset({"name_ar", "name_en", "region_code", "is_active"})


@dataclass(frozen=True, slots=True)
class ZoneLoadResult:
    created: int
    updated: int
    # Rows whose file names were empty - created inactive, named by code.
    unnamed: int


def zones_load(*, country: Country, document: bytes) -> ZoneLoadResult:
    """Upsert every feature of one GeoJSON file into ``country``'s zones.

    All-or-nothing: the file is parsed and validated before any write, and
    the writes share one transaction. Existing rows (matched by ``code``)
    take the file's geometry and region; their names follow the file only
    when the file actually names them, and ``is_active`` is never touched -
    an operator's edits survive a corrected re-upload. New rows without a
    name in the file start inactive so they cannot serve traffic under a
    placeholder name.
    """
    features = zone_features(document=document, country=country)
    created = updated = unnamed = 0
    with transaction.atomic():
        existing = {
            zone.code: zone
            for zone in Zone.objects.filter(code__in=[f.code for f in features])
        }
        for feature in features:
            zone = existing.get(feature.code)
            if zone is None:
                zone = Zone(
                    country=country,
                    code=feature.code,
                    is_active=feature.named,
                    name_ar=feature.name_ar,
                    name_en=feature.name_en,
                )
                created += 1
                unnamed += not feature.named
            else:
                updated += 1
                if feature.named:
                    zone.name_ar = feature.name_ar
                    zone.name_en = feature.name_en
            zone.region_code = feature.region_code
            zone.geometry = feature.geometry
            zone.full_clean()
            zone.save()
    return ZoneLoadResult(created=created, updated=updated, unnamed=unnamed)


def zone_update(*, zone: Zone, data: dict[str, Any]) -> Zone:
    """Apply an operator edit (names, region, active flag)."""
    for field, value in data.items():
        if field not in ZONE_UPDATABLE_FIELDS:
            msg = f"Field not updatable: {field}"
            raise ValueError(msg)
        setattr(zone, field, value)
    zone.full_clean()
    zone.save(update_fields=[*data.keys(), "updated_at"])
    return zone
