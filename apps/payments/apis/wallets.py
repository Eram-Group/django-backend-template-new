"""Wallet endpoints (balance + ledger)."""

from django.db.models import QuerySet
from ninja import Router
from ninja.pagination import paginate

from apps.common.pagination import CursorPagination
from apps.common.requests import AuthedRequest
from apps.payments import selectors
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.payments.schemas import WalletDetail
from apps.payments.schemas import WalletTransactionSummary
from apps.users.models import User

router = Router(tags=["payments"])


@router.get("/wallet", response=WalletDetail, summary="My wallet")
def wallet_detail(request: AuthedRequest[User]) -> Wallet:
    return selectors.get_user_wallet(user=request.auth)


@router.get(
    "/wallet/transactions",
    response=list[WalletTransactionSummary],
    summary="Wallet ledger",
)
@paginate(CursorPagination)
def wallet_transactions(request: AuthedRequest[User]) -> QuerySet[WalletTransaction]:
    return selectors.get_user_wallet_transactions(user=request.auth)
