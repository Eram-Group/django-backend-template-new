"""Saved-card management endpoints."""

import uuid

from django.db.models import QuerySet
from ninja import Router
from ninja.pagination import paginate
from ninja.responses import Status

from apps.common.pagination import CursorPagination
from apps.common.requests import AuthedRequest
from apps.payments import selectors
from apps.payments import services
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.schemas import CardAddIn
from apps.payments.schemas import PaymentDetail
from apps.payments.schemas import SavedCardSummary
from apps.users.models import User

router = Router(tags=["payments"])


@router.get("/cards", response=list[SavedCardSummary], summary="My saved cards")
@paginate(CursorPagination)
def saved_card_list(request: AuthedRequest[User]) -> QuerySet[SavedCard]:
    return selectors.saved_card_list(user=request.auth)


@router.post("/cards/add", response=PaymentDetail, summary="Add a card, no payment")
def saved_card_add(
    request: AuthedRequest[User], payload: CardAddIn | None = None
) -> Payment:
    """Vault a card without charging it: redirect the customer to
    ``checkout_url`` (a card-verification for a nominal amount that is never
    captured). After they complete it, the gateway callback stores the
    card - it then appears in GET /cards.

    With ``card_token`` (from the gateway's card component embedded in the
    app) the hosted card-entry page is skipped: ``checkout_url`` is then
    only the 3DS challenge, and may be empty with a terminal ``status``
    when no challenge was required.
    """
    card_token = payload.card_token if payload else ""
    return services.payment_setup_card(user=request.auth, card_token=card_token)


@router.delete(
    "/cards/{card_id}",
    response={204: None},
    summary="Delete a saved card",
)
def saved_card_delete(request: AuthedRequest[User], card_id: uuid.UUID) -> Status[None]:
    """Removes the card here and best-effort detaches it at the gateway."""
    card = selectors.saved_card_get(user=request.auth, pk=card_id)
    services.saved_card_delete(user=request.auth, saved_card=card)
    return Status(204, None)
