# WalletTransaction admin checklist

- [ ] permissions: can_add / can_change / can_delete deliberately decided
- [ ] permissions: FieldPermissions rules per field reviewed (stubs emitted)
- [ ] list_view: list_display / search_fields / list_filter / ordering
- [ ] list_view: SEARCH_HELP_TEXT translated once search_fields exist
- [ ] change_view: fieldsets cover every editable field; readonly rules
- [ ] resource: export fields explicit and reviewed (no secrets)
- [ ] considered: list_sections previews / actions_detail workflow buttons
- [ ] registered import: apps.payments/admin/__init__.py imports WalletTransactionAdmin
