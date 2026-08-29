from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.payments.admin.wallet import change_view
from apps.payments.admin.wallet import list_view
from apps.payments.admin.wallet import permissions
from apps.payments.admin.wallet.resource import WalletResource
from apps.payments.models import Wallet


@admin.register(Wallet)
class WalletAdmin(BaseModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [WalletResource]

    list_display = list_view.LIST_DISPLAY
    list_filter = list_view.LIST_FILTER
    list_filter_submit = list_view.LIST_FILTER_SUBMIT
    search_fields = list_view.SEARCH_FIELDS
    search_help_text = list_view.SEARCH_HELP_TEXT
    list_select_related = list_view.LIST_SELECT_RELATED
    ordering = list_view.ORDERING
    list_per_page = list_view.LIST_PER_PAGE

    fieldsets = change_view.FIELDSETS
    readonly_fields = change_view.READONLY_FIELDS
