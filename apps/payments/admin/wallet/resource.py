"""Import-export resource for Wallet (explicit fields only)."""

from apps.common.admin import BaseModelResource
from apps.payments.models import Wallet


class WalletResource(BaseModelResource):
    class Meta:
        model = Wallet
        fields = ("id", "created_at", "user", "currency", "balance")
