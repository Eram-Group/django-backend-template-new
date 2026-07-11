# User admin checklist

- [ ] permissions: can_add / can_change / can_delete deliberately decided
- [ ] permissions: FieldPermissions rules for sensitive fields
- [ ] list_view: list_display / search_fields / list_filter / ordering
- [ ] change_view: fieldsets cover every editable field; readonly rules
- [ ] display: computed columns carry admin_order_field
- [ ] resource: export fields explicit and reviewed (no secrets)
- [ ] registered import: apps.users/admin/__init__.py imports UserAdmin
- [ ] admin-basics gate green (G07)
