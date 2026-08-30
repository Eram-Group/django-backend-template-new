"""Import-export resource for Country (explicit fields only)."""

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
