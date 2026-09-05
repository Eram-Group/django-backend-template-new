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
from unfold.forms import BaseDialogForm

from apps.common.admin import BaseModelAdmin
from apps.common.admin import confirm_dialog
from apps.common.exceptions import ApplicationError
from apps.payments import services
from apps.payments.admin.payment.resource import PaymentResource
from apps.payments.constants import PaymentStatus
from apps.payments.models import Payment
from apps.users.models import User


@admin.register(Payment)
class PaymentAdmin(BaseModelAdmin):
    resource_classes = [PaymentResource]

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
        # GET renders the confirmation; the body below runs on POST only.
        dialog=confirm_dialog(
            title=_("Refund this payment?"),
            description=_(
                "The full amount goes back to the customer through the "
                "payment provider. A wallet top-up is debited first; "
                "this cannot be undone."
            ),
            submit=_("Refund"),
        ),
    )
    def refund_payment(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        # Interlock only: the provider call runs in the worker task, never in
        # a request (see payment_refund_start).
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
