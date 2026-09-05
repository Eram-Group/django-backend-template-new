from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.payments.admin.resources import WalletTransactionResource
from apps.payments.models import WalletTransaction


@admin.register(WalletTransaction)
class WalletTransactionAdmin(BaseModelAdmin):
    # Capability + field decisions for the WalletTransaction admin.
    #
    # The ledger is append-only BY SERVICES - the admin never adds, edits, or
    # deletes rows (an admin-added row would move no balance and corrupt the
    # balance_after chain).

    can_add = False
    can_change = False
    can_delete = False
    field_permissions = FieldPermissions()
    list_display = ("wallet", "kind", "amount", "balance_after", "actor", "created_at")
    list_filter = (
        "kind",
        ("created_at", RangeDateFilter),
    )
    list_filter_submit = True
    search_fields = ("wallet__user__email",)
    search_help_text = _("Search by wallet owner email.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("wallet__user", "actor")
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("wallet", "kind", "amount", "balance_after")}),
        ("Provenance", {"fields": ("payment", "actor", "note")}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ()

    resource_classes = [WalletTransactionResource]
