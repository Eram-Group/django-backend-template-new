"""Import-export resource for WalletTransaction (explicit fields only)."""

from apps.common.admin import BaseModelResource
from apps.payments.models import WalletTransaction


class WalletTransactionResource(BaseModelResource):
    class Meta:
        model = WalletTransaction
        fields = (
            "id",
            "created_at",
            "wallet",
            "kind",
            "amount",
            "balance_after",
            "payment",
            "actor",
            "note",
        )
