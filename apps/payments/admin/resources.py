"""Import-export resources for payments: one per admin, explicit fields only.

Exports are read by non-engineers - never raw provider payloads or credentials.
"""

from apps.common.admin import BaseModelResource
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction


class PaymentResource(BaseModelResource):
    """Gateway payloads (``gateway_response`` / ``gateway_callback``) and the
    checkout URL stay OUT: raw provider data, read by non-engineers."""

    class Meta:
        model = Payment
        fields = (
            "id",
            "created_at",
            "user",
            "kind",
            "description",
            "amount",
            "currency",
            "status",
            "gateway",
            "gateway_charge_id",
            "gateway_transaction_id",
            "paid_at",
        )


class SavedCardResource(BaseModelResource):
    """Gateway references (token/customer/agreement) stay OUT of exports on
    purpose - they are charge credentials for our merchant accounts, and
    exports are read by non-engineers."""

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


class WalletResource(BaseModelResource):
    class Meta:
        model = Wallet
        fields = ("id", "created_at", "user", "currency", "balance")


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
