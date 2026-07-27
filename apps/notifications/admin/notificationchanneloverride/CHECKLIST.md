# NotificationChannelOverride admin checklist

- [ ] permissions: can_add / can_change / can_delete deliberately decided
- [ ] permissions: FieldPermissions rules per field reviewed (stubs emitted)
- [ ] list_view: list_display / search_fields / list_filter / ordering
- [ ] list_view: SEARCH_HELP_TEXT translated once search_fields exist
- [ ] inline: decided if NotificationChannelOverride embeds on a parent admin (fill or delete inline.py)
- [ ] change_view: fieldsets cover every editable field; readonly rules
- [ ] display: computed columns carry ordering= (unfold header/label welcome)
- [ ] resource: export fields explicit and reviewed (no secrets)
- [ ] considered: list_sections previews / actions_detail workflow buttons
- [ ] registered import: apps.notifications/admin/__init__.py imports NotificationChannelOverrideAdmin
- [ ] admin-basics gate green (G07)
