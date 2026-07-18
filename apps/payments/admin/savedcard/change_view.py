"""Change-form configuration for SavedCard (read-only)."""

FIELDSETS = (
    (None, {"fields": ("user", "gateway")}),
    (
        "Gateway references",
        {"fields": ("token", "gateway_customer_id", "gateway_agreement_id")},
    ),
    ("Card", {"fields": ("brand", "last4", "exp_month", "exp_year")}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
