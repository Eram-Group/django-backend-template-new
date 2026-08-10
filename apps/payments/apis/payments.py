"""Checkout + status endpoints."""

import uuid

from django.db.models import QuerySet
from ninja import Router
from ninja.pagination import paginate

from apps.common.pagination import CursorPagination
from apps.common.requests import AuthedRequest
from apps.payments import selectors
from apps.payments import services
from apps.payments.models import Payment
from apps.payments.schemas import PaymentCreateIn
from apps.payments.schemas import PaymentDetail
from apps.payments.schemas import PaymentSummary
from apps.users.models import User

router = Router(tags=["payments"])


@router.post("", response=PaymentDetail, summary="Start a checkout")
def payment_create(request: AuthedRequest[User], payload: PaymentCreateIn) -> Payment:
    """Returns the payment with ``checkout_url`` - open it to complete the
    payment; then poll GET /payments/{id} (the webhook flips the status).

    With ``saved_card_id`` the charge may settle synchronously: redirect only
    when ``checkout_url`` is non-empty, otherwise ``status`` is already
    terminal (paid/failed).
    """
    saved_card = (
        selectors.get_saved_card(user=request.auth, pk=payload.saved_card_id)
        if payload.saved_card_id
        else None
    )
    return services.payment_initiate(
        user=request.auth,
        amount=payload.amount,
        currency=payload.currency,
        kind=payload.kind,
        description=payload.description,
        saved_card=saved_card,
    )


@router.get("", response=list[PaymentSummary], summary="My payments")
@paginate(CursorPagination)
def payment_list(request: AuthedRequest[User]) -> QuerySet[Payment]:
    return selectors.list_user_payments(user=request.auth)


@router.get("/{payment_id}", response=PaymentDetail, summary="Payment status")
def payment_detail(request: AuthedRequest[User], payment_id: uuid.UUID) -> Payment:
    """DB read only - the webhook is the source of truth."""
    return selectors.get_user_payment(user=request.auth, pk=payment_id)


@router.post(
    "/{payment_id}/verify", response=PaymentDetail, summary="Re-check with gateway"
)
def payment_verify(request: AuthedRequest[User], payment_id: uuid.UUID) -> Payment:
    """On-demand re-query of the gateway - the fallback when the user
    returned from checkout but the webhook has not arrived yet."""
    payment = selectors.get_user_payment(user=request.auth, pk=payment_id)
    return services.payment_verify(payment=payment)
