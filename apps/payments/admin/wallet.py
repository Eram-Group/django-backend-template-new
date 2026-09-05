from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.payments.admin.resources import WalletResource
from apps.payments.models import Wallet


@admin.register(Wallet)
class WalletAdmin(BaseModelAdmin):
    # Capability + field decisions for the Wallet admin.
    #
    # Balance moves ONLY through services.wallet_apply (row lock + ledger entry);
    # an editable admin field would bypass the ledger. Manual adjustments:
    # `manage.py shell` -> wallet_apply(kind=ADJUSTMENT, actor=..., note=...).

    can_add = False
    can_change = False
    can_delete = False
    field_permissions = FieldPermissions()
    list_display = ("user", "balance", "currency", "updated_at")
    list_filter = ("currency",)
    list_filter_submit = False
    search_fields = ("user__email",)
    search_help_text = _("Search by user email.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("user",)
    ordering = ("-updated_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("user", "balance", "currency")}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ()

    resource_classes = [WalletResource]
