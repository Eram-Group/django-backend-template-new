"""Import-export resources for location: one per admin, explicit fields only.

Exports are read by non-engineers - never raw provider payloads or credentials.
"""

from apps.common.admin import BaseModelResource
from apps.location.models import Country


class CountryResource(BaseModelResource):
    class Meta:
        model = Country
        fields = (
            "id",
            "code",
            "alpha_3",
            "name_ar",
            "name_en",
            "dial_code",
            "phone_example",
            "max_phone_length",
            "currency",
            "is_active",
            "created_at",
        )
