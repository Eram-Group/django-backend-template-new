from typing import Any
from typing import cast

from django.contrib import admin
from django.contrib import messages
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.decorators import action

from apps.common.admin import BaseModelAdmin
from apps.common.exceptions import ApplicationError
from apps.payments import services
from apps.payments.admin.payment import change_view
from apps.payments.admin.payment import list_view
from apps.payments.admin.payment import permissions
from apps.payments.admin.payment.resource import PaymentResource
from apps.payments.constants import PaymentStatus
from apps.payments.models import Payment
from apps.users.models import User


@admin.register(Payment)
class PaymentAdmin(BaseModelAdmin):
    can_add = permissions.CAN_ADD
    can_change = permissions.CAN_CHANGE
    can_delete = permissions.CAN_DELETE
    field_permissions = permissions.FIELD_PERMISSIONS
    resource_classes = [PaymentResource]

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

    # The only write path on this admin (can_change=False): a state
    # transition whose body calls the service - never obj.save().
    actions_detail = ["refund_payment"]

    def has_refund_payment_permission(
        self, request: HttpRequest, object_id: Any = None
    ) -> bool:
        if not request.user.has_perm("payments.change_payment"):
            return False
        if object_id is None:
            return True
        payment = Payment.objects.filter(pk=object_id).first()
        return payment is not None and payment.status == PaymentStatus.PAID

    @action(
        description=_("Refund"),
        url_path="refund",
        permissions=["refund_payment"],
        icon="currency_exchange",
    )
    def refund_payment(self, request: HttpRequest, object_id: str) -> HttpResponse:
        # Interlock only: the provider call runs in the worker task, outside
        # this request's ATOMIC_REQUESTS transaction (see payment_refund_start).
        payment = Payment.objects.get(pk=object_id)
        try:
            services.payment_refund_start(
                payment=payment, actor=cast("User", request.user)
            )
        except ApplicationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request, _("Refund started - refresh to see the final status.")
            )
        return redirect(reverse("admin:payments_payment_change", args=[object_id]))
