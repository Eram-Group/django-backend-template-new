from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.payments.admin.wallettransaction.resource import WalletTransactionResource
from apps.payments.models import WalletTransaction


@admin.register(WalletTransaction)
class WalletTransactionAdmin(BaseModelAdmin):
    resource_classes = [WalletTransactionResource]
