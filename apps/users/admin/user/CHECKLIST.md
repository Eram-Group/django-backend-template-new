# User admin checklist

- [x] permissions: can_add / can_change / can_delete deliberately decided
- [x] permissions: FieldPermissions rules for sensitive fields
- [x] list_view: list_display / search_fields / list_filter / ordering
- [x] change_view: fieldsets cover every editable field; readonly rules
- [x] resource: export fields explicit and reviewed (no secrets)
- [x] registered import: apps.users/admin/__init__.py imports UserAdmin
