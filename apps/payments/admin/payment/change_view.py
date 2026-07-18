"""Change-form configuration for Payment (read-only inspection view)."""

FIELDSETS = (
    (None, {"fields": ("user", "kind", "description", "amount", "currency")}),
    (
        "Gateway",
        {
            "fields": (
                "gateway",
                "status",
                "idempotency_key",
                "gateway_charge_id",
                "gateway_transaction_id",
                "checkout_url",
                "gateway_response",
                "gateway_callback",
            )
        },
    ),
    (
        "Dates",
        {"fields": ("paid_at", "refund_attempted_at", "created_at", "updated_at")},
    ),
)
READONLY_FIELDS = ()
