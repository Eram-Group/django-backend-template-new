"""Payment writes: checkout, gateway-event transitions, verify, refund.

The webhook is the source of truth for payment state; ``payment_verify`` is
the on-demand fallback when a webhook never arrived. All transitions run
under select_for_update on the Payment row and are idempotent against
replayed events (TERMINAL_STATUSES are never overwritten - a second
"paid" webhook cannot credit the wallet twice).
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.http import OutboundError
from apps.notifications.constants import NotificationKind
from apps.notifications.services import notification_send
from apps.payments.constants import TERMINAL_STATUSES
from apps.payments.constants import Currency
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.exceptions import PaymentNotRefundableError
from apps.payments.exceptions import PaymentRefundFailedError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways import gateway_for_currency
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import WebhookEvent
from apps.payments.models import Payment
from apps.payments.services.wallets import wallet_apply
from apps.payments.services.wallets import wallet_get_or_create
from apps.users.models import User


def payment_initiate(
    *,
    user: User,
    amount: Decimal,
    currency: Currency | str,
    kind: PaymentKind | str = PaymentKind.OTHER,
    description: str = "",
) -> Payment:
    """Create the PENDING row, then ask the gateway for a checkout URL.

    The client waits for checkout_url, so the gateway call is synchronous
    (kernel timeout 10s < gunicorn's 30s); the idempotency key is planted at
    the gateway, so a retried initiate can never double-charge.
    """
    gateway = gateway_for_currency(str(currency))
    payment = Payment(
        user=user,
        amount=amount,
        currency=currency,
        kind=kind,
        description=description,
        gateway=gateway.name,
    )
    payment.full_clean()
    payment.save()
    request = CheckoutRequest(
        reference=str(payment.idempotency_key),
        amount=payment.amount,
        currency=payment.currency,
        description=description,
        customer_email=user.email,
        customer_name=user.name,
        customer_phone=str(user.phone) if user.phone else "",
        webhook_url=(
            f"{settings.BACKEND_BASE_URL}/api/v1/payments/webhooks/{gateway.name}"
        ),
        redirect_url=f"{settings.FRONTEND_BASE_URL}/payments/{payment.pk}/return",
    )
    try:
        session = gateway.create_checkout(request=request)
    except (OutboundError, GatewayResponseError) as exc:
        payment.status = PaymentStatus.FAILED
        payment.save(update_fields=["status", "updated_at"])
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    payment.gateway_charge_id = session.charge_id
    payment.checkout_url = session.checkout_url
    payment.gateway_response = session.raw
    payment.full_clean()
    payment.save(
        update_fields=[
            "gateway_charge_id",
            "checkout_url",
            "gateway_response",
            "updated_at",
        ]
    )
    return payment


def payment_apply_gateway_event(*, gateway_name: str, event: WebhookEvent) -> Payment:
    """Apply one gateway event (webhook or verify) - idempotent on replays."""
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(
                idempotency_key=event.reference, gateway=gateway_name
            )
        except (Payment.DoesNotExist, ValueError, ValidationError) as exc:
            raise PaymentNotFoundError(str(_("Payment not found."))) from exc
        payment.gateway_callback = event.raw
        payment.gateway_transaction_id = event.transaction_id
        if payment.status in TERMINAL_STATUSES:  # replay: record, never re-credit
            payment.save(
                update_fields=[
                    "gateway_callback",
                    "gateway_transaction_id",
                    "updated_at",
                ]
            )
            return payment
        if event.is_paid:
            payment.status = PaymentStatus.PAID
            payment.paid_at = timezone.now()
        else:
            payment.status = PaymentStatus.FAILED
        payment.full_clean()
        payment.save(
            update_fields=[
                "status",
                "paid_at",
                "gateway_callback",
                "gateway_transaction_id",
                "updated_at",
            ]
        )
        if payment.status == PaymentStatus.PAID:
            _on_paid(payment)
        return payment


def _on_paid(payment: Payment) -> None:
    if payment.kind == PaymentKind.WALLET_TOPUP:
        wallet = wallet_get_or_create(user=payment.user, currency=payment.currency)
        entry = wallet_apply(
            wallet_id=wallet.pk,
            amount=payment.amount,
            kind=WalletTransactionKind.TOPUP,
            payment=payment,
        )
        notification_send(
            recipient=payment.user,
            kind=NotificationKind.WALLET_CREDITED,
            context={
                "amount": str(payment.amount),
                "currency": payment.currency,
                "balance": str(entry.balance_after),
            },
        )
    else:
        notification_send(
            recipient=payment.user,
            kind=NotificationKind.PAYMENT_PAID,
            context={"amount": str(payment.amount), "currency": payment.currency},
        )


def payment_verify(*, payment: Payment) -> Payment:
    """Re-query the gateway on demand (webhook fallback, user is polling)."""
    if payment.status in TERMINAL_STATUSES:
        return payment
    gateway = gateway_by_name(payment.gateway)
    if gateway is None:
        raise PaymentNotFoundError(str(_("Payment gateway is not configured.")))
    try:
        status = gateway.fetch_status(
            charge_id=payment.gateway_charge_id,
            reference=str(payment.idempotency_key),
        )
    except (OutboundError, GatewayResponseError) as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    if not status.is_paid:
        return payment
    event = WebhookEvent(
        reference=str(payment.idempotency_key),
        transaction_id=status.transaction_id,
        is_paid=True,
        status=status.status,
        raw=status.raw,
    )
    return payment_apply_gateway_event(gateway_name=payment.gateway, event=event)


def payment_refund(*, payment: Payment, actor: User) -> Payment:
    """Full refund (partial refunds: extend with an amount arg + partial ledger).

    A refunded wallet top-up debits the wallet - if the user already spent
    the credit, InsufficientBalanceError blocks the refund (resolve the
    balance first).
    """
    if payment.status != PaymentStatus.PAID:
        raise PaymentNotRefundableError(str(_("Only paid payments can be refunded.")))
    gateway = gateway_by_name(payment.gateway)
    if gateway is None:
        raise PaymentNotFoundError(str(_("Payment gateway is not configured.")))
    try:
        result = gateway.refund(
            transaction_id=payment.gateway_transaction_id or payment.gateway_charge_id,
            amount=payment.amount,
            currency=payment.currency,
        )
    except (OutboundError, GatewayResponseError) as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    if not result.ok:
        raise PaymentRefundFailedError(str(_("The gateway rejected the refund.")))
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status == PaymentStatus.REFUNDED:
            return locked
        if locked.kind == PaymentKind.WALLET_TOPUP:
            wallet = wallet_get_or_create(user=locked.user, currency=locked.currency)
            wallet_apply(
                wallet_id=wallet.pk,
                amount=-locked.amount,
                kind=WalletTransactionKind.REFUND,
                payment=locked,
                actor=actor,
            )
        locked.status = PaymentStatus.REFUNDED
        locked.full_clean()
        locked.save(update_fields=["status", "updated_at"])
        return locked
