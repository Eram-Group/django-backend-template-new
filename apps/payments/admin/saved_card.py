from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.payments.admin.resources import SavedCardResource
from apps.payments.models import SavedCard


@admin.register(SavedCard)
class SavedCardAdmin(BaseModelAdmin):
    # Capability + field decisions for the SavedCard admin.
    #
    # Rows are created BY WEBHOOKS and deleted through saved_card_delete (which
    # also detaches the token at the gateway) - an admin add would invent a token
    # the provider never issued, and an admin hard-delete would skip the
    # gateway-side detach. Support removes cards via the API/service path.

    can_add = False
    can_change = False
    can_delete = False
    field_permissions = FieldPermissions()
    list_display = (
        "user",
        "gateway",
        "brand",
        "last4",
        "exp_month",
        "exp_year",
        "created_at",
    )
    list_filter = (
        "gateway",
        "brand",
        ("created_at", RangeDateFilter),
    )
    list_filter_submit = True
    search_fields = ("user__email", "token")
    search_help_text = _("Search by owner email or gateway token.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("user",)
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("user", "gateway")}),
        (
            "Gateway references",
            {
                "fields": (
                    "token",
                    "gateway_customer_id",
                    "gateway_agreement_id",
                    "fingerprint",
                )
            },
        ),
        ("Card", {"fields": ("brand", "last4", "exp_month", "exp_year")}),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )
    readonly_fields = ()

    resource_classes = [SavedCardResource]
