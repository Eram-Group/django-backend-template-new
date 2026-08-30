"""Import-export resource for Zone (explicit fields only).

The geometry is exported as WKT (GeoDjango's string form) - large but
lossless, and the only way to get a shape back out of the database.
"""

from apps.common.admin import BaseModelResource
from apps.location.models import Zone


class ZoneResource(BaseModelResource):
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
