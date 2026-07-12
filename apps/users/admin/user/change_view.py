"""Change-form configuration for User."""

FIELDSETS = (
    (None, {"fields": ("email", "name", "phone", "language")}),
    ("Status", {"fields": ("is_active", "is_staff", "is_superuser", "groups")}),
    ("Dates", {"fields": ("last_login", "date_joined", "created_at", "updated_at")}),
)
READONLY_FIELDS = ("last_login", "date_joined")
