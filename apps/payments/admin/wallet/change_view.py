"""Change-form configuration for Wallet (read-only inspection view)."""

FIELDSETS = (
    (None, {"fields": ("user", "balance", "currency")}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
