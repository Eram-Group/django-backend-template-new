from django.contrib import admin

from apps.common.admin import BaseModelAdmin
from apps.payments.admin.savedcard.resource import SavedCardResource
from apps.payments.models import SavedCard


@admin.register(SavedCard)
class SavedCardAdmin(BaseModelAdmin):
    resource_classes = [SavedCardResource]
