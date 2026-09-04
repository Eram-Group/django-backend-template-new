"""Writes for zones - the load road and the operator edits."""

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.location.models import Country
from apps.zones.exceptions import ZonesError
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
    existing = set(
        Zone.objects.filter(code__in=[f.code for f in features]).values_list(
            "code", flat=True
        )
    )
    rows = [
        Zone(
            country=country,
            code=feature.code,
            region_code=feature.region_code,
            geometry=feature.geometry,
            is_active=feature.named,
            name_ar=feature.name_ar,
            name_en=feature.name_en,
        )
        for feature in features
    ]
    for zone in rows:
        zone.full_clean(exclude=["code"])  # unique(code) is what the upsert keys on
    named = [
        zone for zone, feature in zip(rows, features, strict=True) if feature.named
    ]
    unnamed = [
        zone for zone, feature in zip(rows, features, strict=True) if not feature.named
    ]
    with transaction.atomic():
        # One INSERT ... ON CONFLICT (code) DO UPDATE per group: a named
        # feature also refreshes the names, an unnamed one touches only
        # region and geometry (the operator's names and is_active survive).
        for group, update_fields in (
            (named, ["region_code", "geometry", "name_ar", "name_en"]),
            (unnamed, ["region_code", "geometry"]),
        ):
            Zone.objects.bulk_create(
                group,
                update_conflicts=True,
                update_fields=update_fields,
                unique_fields=["code"],
            )
    created = sum(zone.code not in existing for zone in rows)
    return ZoneLoadResult(
        created=created,
        updated=len(rows) - created,
        unnamed=sum(zone.code not in existing for zone in unnamed),
    )


def zone_update(*, zone: Zone, data: dict[str, Any]) -> Zone:
    """Apply an operator edit (names, region, active flag)."""
    for field, value in data.items():
        if field not in ZONE_UPDATABLE_FIELDS:
            raise ZonesError(
                str(_("Field not updatable: %(field)s") % {"field": field})
            )
        setattr(zone, field, value)
    zone.full_clean()
    zone.save(update_fields=[*data.keys(), "updated_at"])
    return zone
