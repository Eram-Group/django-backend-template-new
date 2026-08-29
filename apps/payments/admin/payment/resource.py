"""Import-export resource for Payment (explicit fields only).

Gateway payloads (``gateway_response`` / ``gateway_callback``) and the
checkout URL stay OUT: raw provider data, read by non-engineers.
"""

from apps.common.admin import BaseModelResource
from apps.payments.models import Payment


class PaymentResource(BaseModelResource):
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
