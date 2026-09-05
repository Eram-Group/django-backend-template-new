from typing import Any
from typing import cast

from django.contrib import admin
from django.contrib import messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import RangeDateFilter
from unfold.decorators import action
from unfold.forms import BaseDialogForm

from apps.common.admin import BaseModelAdmin
from apps.common.admin import FieldPermissions
from apps.common.admin import confirm_dialog
from apps.common.exceptions import ApplicationError
from apps.payments import selectors
from apps.payments import services
from apps.payments.admin.resources import PaymentResource
from apps.payments.constants import PaymentStatus
from apps.payments.models import Payment
from apps.users.models import User


class NeedsAttentionFilter(admin.SimpleListFilter):
    """The rows an operator must look at - the sidebar badge's list."""

    title = _("needs attention")
    parameter_name = "attention"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, Any]]:
        return [("yes", _("Needs attention"))]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Payment]) -> Any:
        if self.value() == "yes":
            return queryset & selectors.payments_needing_attention()
        return queryset


@admin.register(Payment)
class PaymentAdmin(BaseModelAdmin):
    # Capability + field decisions for the Payment admin.
    #
    # Rows are created by payment_initiate and transitioned by gateway events -
    # the admin inspects; the ONLY write is the Refund detail action (which calls
    # the service). Nothing here is ever hand-edited or deleted.

    can_add = False
    can_change = False
    can_delete = False  # financial history is append-only
    field_permissions = FieldPermissions()
    list_display = (
        "user",
        "amount",
        "currency",
        "kind",
        "status",
        "gateway",
        "created_at",
    )
    list_filter = (
        NeedsAttentionFilter,
        "status",
        "gateway",
        "currency",
        "kind",
        ("created_at", RangeDateFilter),
    )
    list_filter_submit = True  # form-based (range) filters apply on submit
    search_fields = ("user__email", "gateway_charge_id", "gateway_transaction_id")
    search_help_text = _("Search by user email or gateway charge/transaction id.")

    # FK columns render without a per-row query on the changelist.
    list_select_related = ("user",)
    ordering = ("-created_at",)
    list_per_page = 50
    fieldsets = (
        (None, {"fields": ("user", "kind", "description", "amount", "currency")}),
        (
            "Gateway",
            {
                "fields": (
                    "gateway",
                    "status",
                    "idempotency_key",
                    "gateway_charge_id",
                    "gateway_transaction_id",
                    "checkout_url",
                    "gateway_response",
                    "gateway_callback",
                )
            },
        ),
        (
            "Dates",
            {"fields": ("paid_at", "refund_attempted_at", "created_at", "updated_at")},
        ),
    )
    readonly_fields = ()

    resource_classes = [PaymentResource]

    # The only write paths on this admin (can_change=False): state
    # transitions whose bodies call a service - never obj.save().
    actions_detail = ["verify_payment", "refund_payment"]

    def has_verify_payment_permission(
        self, request: HttpRequest, object_id: Any = None
    ) -> bool:
        if not request.user.has_perm("payments.change_payment"):
            return False
        if object_id is None:
            return True
        payment = Payment.objects.filter(pk=object_id).first()
        return payment is not None and payment.status == PaymentStatus.PENDING

    @action(
        description=_("Verify with provider"),
        url_path="verify",
        permissions=["verify_payment"],
        icon="sync",
        dialog=confirm_dialog(
            title=_("Ask the provider about this payment?"),
            description=_(
                "For a checkout whose webhook never arrived: the provider's "
                "answer is applied through the same guarded transition a "
                "webhook takes. A still-unpaid checkout stays pending."
            ),
            submit=_("Verify"),
        ),
    )
    def verify_payment(
        self, request: HttpRequest, form: BaseDialogForm, object_id: str
    ) -> HttpResponse:
        payment = Payment.objects.get(pk=object_id)
        try:
            verified = services.payment_verify(payment=payment)
        except ApplicationError as exc:
            messages.error(request, exc.message)
        else:
            messages.success(
                request,
                _("Provider answered: the payment is %(status)s.")
                % {"status": verified.get_status_display()},
            )
        return redirect(reverse("admin:payments_payment_change", args=[object_id]))

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
