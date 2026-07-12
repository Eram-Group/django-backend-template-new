"""Change-form configuration for WalletTransaction (read-only)."""

FIELDSETS = (
    (None, {"fields": ("wallet", "kind", "amount", "balance_after")}),
    ("Provenance", {"fields": ("payment", "actor", "note")}),
    ("Dates", {"fields": ("created_at", "updated_at")}),
)
READONLY_FIELDS = ()
