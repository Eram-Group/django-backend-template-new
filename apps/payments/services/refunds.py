"""Refund writes: interlock, provider call, provider follow-up, finalization.

A refund is three commits, never one transaction: ``payment_refund_start``
(the interlock: REFUND_PENDING + wallet debit, in the request),
``payment_refund_execute`` (the non-idempotent provider call, in the
worker, behind a write-ahead marker) and finalization - immediately when
the provider settles synchronously, otherwise by ``payment_refund_verify``
from the reconcile sweep once the provider reports completion. A provider
"accepted" is never treated as "done".
"""

import uuid

import structlog
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.http import OutboundError
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotRefundableError
from apps.payments.exceptions import PaymentRefundFailedError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways.base import GatewayError
from apps.payments.gateways.base import RefundResult
from apps.payments.gateways.base import RefundStatus
from apps.payments.gateways.base import provider_outcome_unknown
from apps.payments.models import Payment
from apps.payments.services.wallets import wallet_apply
from apps.payments.services.wallets import wallet_for_currency
from apps.payments.tasks.refunds import process_payment_refund
from apps.users.models import User

logger = structlog.get_logger(__name__)


def payment_refund_start(*, payment: Payment, actor: User) -> Payment:
    """Refund phase 1, the interlock - the provider is NOT called here.

    Full refund only (partial refunds: extend with an amount arg + partial
    ledger). Locks the row, requires PAID with a settled transaction id (a
    concurrent second refund fails this check before ever reaching the
    gateway), debits the wallet for top-ups (a spent credit raises
    InsufficientBalanceError before the provider is contacted), flips to
    REFUND_PENDING, and enqueues ``process_payment_refund`` on commit.

    The provider call lives in the worker task on purpose: the executor's
    phases must each commit on their own, and a request handler (an admin
    action inside Django's own atomic change view) would turn them into
    savepoints - holding the Payment+Wallet row locks across outbound HTTP
    and rolling the interlock back to PAID on a crash even after the
    provider refunded. Committing REFUND_PENDING here and doing the rest in
    the worker closes both holes.
    """
    gateway_by_name(payment.gateway)  # an unconfigured gateway refuses up front
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != PaymentStatus.PAID:
            raise PaymentNotRefundableError(
                str(_("Only paid payments can be refunded."))
            )
        if not locked.gateway_transaction_id:
            raise PaymentNotRefundableError(
                str(_("This payment has no settled transaction to refund."))
            )
        if locked.kind == PaymentKind.WALLET_TOPUP:
            wallet = wallet_for_currency(user=locked.user, currency=locked.currency)
            wallet_apply(
                wallet_id=wallet.pk,
                amount=-locked.amount,
                kind=WalletTransactionKind.REFUND,
                payment=locked,
                actor=actor,
                note="",
            )
        locked.status = PaymentStatus.REFUND_PENDING
        locked.refund_attempted_at = None
        locked.gateway_refund_id = ""
        locked.full_clean()
        locked.save(
            update_fields=[
                "status",
                "refund_attempted_at",
                "gateway_refund_id",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: process_payment_refund.enqueue(
                payment_id=str(locked.pk), actor_id=str(actor.pk)
            )
        )
        return locked


def payment_refund_execute(*, payment_id: uuid.UUID, actor: User | None) -> Payment:
    """Refund phases 2+3: provider call + finalization. Worker/sweep only -
    never call from inside an open transaction (a request handler's atomic
    block would reopen the crash window the start/execute split closes).

    ``actor`` is the staff member who started the refund (the worker task
    carries it); the reconcile sweep passes None - nobody is behind a
    machine-driven retry.

    Safe to re-run: a row no longer REFUND_PENDING is a no-op, and a row
    whose ``refund_attempted_at`` marker is already set is never re-sent to
    the provider (``gateway.refund`` is not idempotent at Tap/Paymob) - it
    is logged for manual reconciliation instead.

    Outcome contract: SUCCEEDED finalizes (REFUNDED); FAILED reverts the
    interlock (row back to PAID, wallet compensated - safe to retry);
    PENDING keeps REFUND_PENDING with the provider's refund id stored, and
    ``payment_refund_verify`` (reconcile sweep) finishes it.

    Error contract: ``PaymentGatewayUnavailableError`` /
    ``PaymentRefundFailedError`` mean the interlock was reverted. A raw
    ``OutboundError`` / ``GatewayError`` escaping means the provider MAY
    have processed the refund: the row stays REFUND_PENDING with the marker
    set, the task fails loudly, and a human reconciles against the provider
    dashboard.
    """
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if locked.status != PaymentStatus.REFUND_PENDING:
            return locked  # replayed task after finalization - nothing to do
        gateway = gateway_by_name(locked.gateway)
        if locked.refund_attempted_at is not None:
            logger.error(
                "payment_refund_needs_reconciliation",
                payment_id=str(locked.pk),
                gateway=locked.gateway,
                refund_attempted_at=locked.refund_attempted_at.isoformat(),
            )
            return locked
        # Write-ahead marker, committed before the provider call: a crash or
        # replay after this point can never refund twice at the gateway.
        locked.refund_attempted_at = timezone.now()
        locked.save(update_fields=["refund_attempted_at", "updated_at"])
    try:
        result = gateway.refund(
            transaction_id=locked.gateway_transaction_id,
            amount=locked.amount,
            currency=locked.currency,
        )
    except (OutboundError, GatewayError) as exc:
        if provider_outcome_unknown(exc):
            logger.exception(
                "payment_refund_needs_reconciliation",
                payment_id=str(locked.pk),
                gateway=locked.gateway,
                error=type(exc).__name__,
            )
            raise
        _refund_revert(payment_id=locked.pk, actor=actor)
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    return _refund_settle(payment_id=locked.pk, result=result, actor=actor)


def payment_refund_verify(*, payment: Payment) -> Payment:
    """Ask the provider how an accepted refund ended (reconcile sweep).

    Only for REFUND_PENDING rows that carry a provider refund id - the
    provider accepted the refund but had not settled it when asked. A
    provider error leaves the row untouched for the next sweep.
    """
    if payment.status != PaymentStatus.REFUND_PENDING or not payment.gateway_refund_id:
        return payment
    gateway = gateway_by_name(payment.gateway)
    try:
        result = gateway.fetch_refund(refund_id=payment.gateway_refund_id)
    except (OutboundError, GatewayError) as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    return _refund_settle(payment_id=payment.pk, result=result, actor=None)


def _refund_settle(
    *, payment_id: uuid.UUID, result: RefundResult, actor: User | None
) -> Payment:
    """Record the provider's answer and move the row accordingly."""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if locked.status != PaymentStatus.REFUND_PENDING:
            return locked
        locked.gateway_refund_id = result.refund_id
        locked.gateway_refund_response = result.raw
        if result.status == RefundStatus.SUCCEEDED:
            locked.status = PaymentStatus.REFUNDED
        locked.full_clean()
        locked.save(
            update_fields=[
                "status",
                "gateway_refund_id",
                "gateway_refund_response",
                "updated_at",
            ]
        )
    if result.status == RefundStatus.FAILED:
        _refund_revert(payment_id=payment_id, actor=actor)
        raise PaymentRefundFailedError(str(_("The gateway rejected the refund.")))
    if result.status == RefundStatus.PENDING:
        logger.info(
            "payment_refund_pending_at_provider",
            payment_id=str(payment_id),
            gateway=locked.gateway,
            refund_id=result.refund_id,
        )
    return locked


def _refund_revert(*, payment_id: uuid.UUID, actor: User | None) -> None:
    """Undo the refund interlock: compensate the wallet debit, restore PAID."""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if locked.kind == PaymentKind.WALLET_TOPUP:
            wallet = wallet_for_currency(user=locked.user, currency=locked.currency)
            wallet_apply(
                wallet_id=wallet.pk,
                amount=locked.amount,
                kind=WalletTransactionKind.ADJUSTMENT,
                payment=locked,
                actor=actor,
                note="Refund reverted: the gateway refund did not go through.",
            )
        locked.status = PaymentStatus.PAID
        locked.refund_attempted_at = None
        locked.gateway_refund_id = ""
        locked.full_clean()
        locked.save(
            update_fields=[
                "status",
                "refund_attempted_at",
                "gateway_refund_id",
                "updated_at",
            ]
        )
