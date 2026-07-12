"""Wallet endpoints (balance + ledger)."""

from django.db.models import QuerySet
from django.http import HttpRequest
from ninja import Router
from ninja.pagination import paginate

from apps.common.pagination import CursorPagination
from apps.payments import selectors
from apps.payments import services
from apps.payments.constants import Currency
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.payments.schemas import WalletDetail
from apps.payments.schemas import WalletTransactionSummary
from apps.users.models import User


class AuthedRequest(HttpRequest):
    """Typing-only: ninja sets request.auth to the authenticated User."""

    auth: User


router = Router(tags=["payments"])


@router.get("/wallet", response=WalletDetail, summary="My wallet")
def wallet_detail(request: AuthedRequest) -> Wallet:
    return services.wallet_get_or_create(user=request.auth, currency=Currency.SAR)


@router.get(
    "/wallet/transactions",
    response=list[WalletTransactionSummary],
    summary="Wallet ledger",
)
@paginate(CursorPagination)
def wallet_transactions(request: AuthedRequest) -> QuerySet[WalletTransaction]:
    return selectors.wallet_transaction_list(user=request.auth)
