"""Import-export resources for zones: one per admin, explicit fields only.

Exports are read by non-engineers - never raw provider payloads or credentials.
"""

from apps.common.admin import BaseModelResource
from apps.zones.models import Zone


class ZoneResource(BaseModelResource):
    """The geometry is exported as WKT (GeoDjango's string form) - large but
    lossless, and the only way to get a shape back out of the database."""

    class Meta:
        model = Zone
        fields = (
            "id",
            "code",
            "country__code",
            "region_code",
            "name_ar",
            "name_en",
            "is_active",
            "geometry",
            "created_at",
        )
