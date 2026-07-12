"""Checkout + status endpoints."""

import uuid

from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import Router
from ninja.pagination import paginate

from apps.common.pagination import CursorPagination
from apps.payments import selectors
from apps.payments import services
from apps.payments.models import Payment
from apps.payments.schemas import PaymentCreateIn
from apps.payments.schemas import PaymentDetail
from apps.payments.schemas import PaymentSummary
from apps.users.models import User


class AuthedRequest(HttpRequest):
    """Typing-only: ninja sets request.auth to the authenticated User."""

    auth: User


router = Router(tags=["payments"])


@router.post("", response=PaymentDetail, summary="Start a checkout")
def payment_create(request: AuthedRequest, payload: PaymentCreateIn) -> Payment:
    """Returns the payment with ``checkout_url`` - open it to complete the
    payment; then poll GET /payments/{id} (the webhook flips the status)."""
    return services.payment_initiate(
        user=request.auth,
        amount=payload.amount,
        currency=payload.currency,
        kind=payload.kind,
        description=payload.description,
    )


@router.get("", response=list[PaymentSummary], summary="My payments")
@paginate(CursorPagination)
def payment_list(request: AuthedRequest) -> QuerySet[Payment]:
    return selectors.payment_list(user=request.auth)


@router.get("/{payment_id}", response=PaymentDetail, summary="Payment status")
def payment_detail(request: AuthedRequest, payment_id: uuid.UUID) -> Payment:
    """DB read only - the webhook is the source of truth."""
    return selectors.payment_get(user=request.auth, pk=payment_id)


@router.post(
    "/{payment_id}/verify", response=PaymentDetail, summary="Re-check with gateway"
)
def payment_verify(request: AuthedRequest, payment_id: uuid.UUID) -> Payment:
    """On-demand re-query of the gateway - the fallback when the user
    returned from checkout but the webhook has not arrived yet."""
    payment = selectors.payment_get(user=request.auth, pk=payment_id)
    return services.payment_verify(payment=payment)
