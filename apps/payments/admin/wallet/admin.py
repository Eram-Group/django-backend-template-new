from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.payments.admin.wallet.resource import WalletResource
from apps.payments.models import Wallet


@admin.register(Wallet)
class WalletAdmin(BaseModelAdmin):
    resource_classes = [WalletResource]
