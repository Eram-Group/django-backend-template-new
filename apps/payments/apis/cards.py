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
from apps.payments.models import SavedCard
from apps.payments.schemas import SavedCardSummary
from apps.users.models import User

router = Router(tags=["payments"])


@router.get("/cards", response=list[SavedCardSummary], summary="My saved cards")
@paginate(CursorPagination)
def saved_card_list(request: AuthedRequest[User]) -> QuerySet[SavedCard]:
    return selectors.saved_card_list(user=request.auth)


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
