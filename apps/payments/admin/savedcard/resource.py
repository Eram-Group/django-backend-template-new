"""Import-export resource for SavedCard (explicit fields only).

Gateway references (token/customer/agreement) stay OUT of exports on
purpose - they are charge credentials for our merchant accounts, and
exports are read by non-engineers.
"""

from apps.common.admin import BaseModelResource
from apps.payments.models import SavedCard


class SavedCardResource(BaseModelResource):
    class Meta:
        model = SavedCard
        fields = (
            "id",
            "user",
            "gateway",
            "brand",
            "last4",
            "exp_month",
            "exp_year",
            "created_at",
        )
