"""Payment writes: checkout, gateway-event transitions, verify, refund.

The webhook is the source of truth for payment state; ``payment_verify`` is
the on-demand fallback when a webhook never arrived. All transitions run
under select_for_update on the Payment row and are idempotent against
replayed events (TERMINAL_STATUSES are never overwritten - a second
"paid" webhook cannot credit the wallet twice).
"""

import uuid
from decimal import Decimal

import structlog
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.http import OutboundError
from apps.common.http import OutboundStatusError
from apps.common.http import OutboundTransportError
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
from apps.payments.exceptions import SavedCardGatewayMismatchError
from apps.payments.exceptions import SavedCardNotFoundError
from apps.payments.exceptions import WalletCurrencyMismatchError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways import gateway_for_currency
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import GatewayResponseError
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import WebhookEvent
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.models import Wallet
from apps.payments.selectors.wallets import wallet_get
from apps.payments.services.saved_cards import saved_card_store
from apps.payments.services.wallets import wallet_apply
from apps.payments.tasks.refunds import process_payment_refund
from apps.users.models import User

logger = structlog.get_logger(__name__)


def _card_ref(card: SavedCard) -> SavedCardRef:
    return SavedCardRef(
        token=card.token,
        customer_id=card.gateway_customer_id,
        agreement_id=card.gateway_agreement_id,
    )


def _session_event(payment: Payment, session: CheckoutSession) -> WebhookEvent:
    """A synchronous charge outcome, shaped like the webhook that will also
    arrive - both converge on the idempotent payment_apply_gateway_event."""
    return WebhookEvent(
        reference=str(payment.idempotency_key),
        transaction_id=session.transaction_id or session.charge_id,
        is_paid=session.is_paid,
        status=session.status,
        raw=session.raw,
    )


def _validate_saved_card(
    *, user: User, saved_card: SavedCard, gateway_name: str
) -> None:
    if saved_card.user_id != user.pk:
        raise SavedCardNotFoundError(str(_("Saved card not found.")))
    if saved_card.gateway != gateway_name:
        raise SavedCardGatewayMismatchError(
            str(_("This card cannot be used with this currency."))
        )


def _wallet_for(*, user: User, currency: str) -> Wallet:
    """Resolve the user's signup-provisioned wallet for a payment in
    ``currency``, rejecting a currency mismatch before any money moves."""
    wallet = wallet_get(user=user)
    if wallet.currency != currency:
        raise WalletCurrencyMismatchError(
            str(_("Wallet currency does not match the payment currency."))
        )
    return wallet


def payment_initiate(
    *,
    user: User,
    amount: Decimal,
    currency: Currency | str,
    kind: PaymentKind | str = PaymentKind.OTHER,
    description: str = "",
    save_card: bool = False,
    saved_card: SavedCard | None = None,
) -> Payment:
    """Create the PENDING row, then ask the gateway for a checkout URL.

    The client waits for checkout_url, so the gateway call is synchronous
    (kernel timeout 10s < gunicorn's 30s); the idempotency key is planted at
    the gateway, so a retried initiate can never double-charge.

    ``save_card`` asks the gateway to vault the card entered at checkout
    (ignored alongside ``saved_card`` - a stored card cannot be re-saved).
    ``saved_card`` pays one-click WITH a stored card: Paymob still returns a
    checkout_url (hosted CVV entry); Tap charges server-side and may settle
    synchronously - the response is then already terminal with an empty
    checkout_url, or carries a 3DS-challenge URL to redirect to.

    Accepted risk: under ATOMIC_REQUESTS the PENDING row becomes visible to
    webhooks only when the request commits, so a webhook racing the commit
    404s - the gateway's webhook retry, ``payment_verify``, and the
    ``reconcile_payments`` sweep all pick it up.
    """
    if kind == PaymentKind.WALLET_TOPUP:
        # Fail before the provider charges: waiting for the credit path to
        # reject the mismatch would strand already-captured money.
        _wallet_for(user=user, currency=str(currency))
    gateway = gateway_for_currency(str(currency))
    if saved_card is not None:
        _validate_saved_card(
            user=user, saved_card=saved_card, gateway_name=gateway.name
        )
    payment = Payment(
        user=user,
        amount=amount,
        currency=currency,
        kind=kind,
        description=description,
        gateway=gateway.name,
        saved_card=saved_card,
        save_card_requested=save_card and saved_card is None,
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
        save_card=payment.save_card_requested,
        saved_card=_card_ref(saved_card) if saved_card is not None else None,
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
    if not session.checkout_url and session.status:
        # Synchronous outcome (one-click captured/declined) - apply it now;
        # the webhook that follows is an idempotent replay.
        return payment_apply_gateway_event(
            gateway_name=gateway.name, event=_session_event(payment, session)
        )
    return payment


def payment_charge_saved(
    *,
    user: User,
    saved_card: SavedCard,
    amount: Decimal,
    currency: Currency | str,
    kind: PaymentKind | str = PaymentKind.OTHER,
    description: str = "",
) -> Payment:
    """MIT: charge a stored card server-side, no customer present.

    Never auto-retry a failure here - a merchant-initiated charge that ran
    twice is a double-charge, not a retry. The gateways always plant our
    reference AND the webhook URL, so a crash between the provider call and
    the row update self-heals when the webhook lands; FAILED is non-terminal,
    so a late CAPTURED webhook corrects a wrongly-FAILED row too.
    """
    if kind == PaymentKind.WALLET_TOPUP:
        _wallet_for(user=user, currency=str(currency))
    gateway = gateway_for_currency(str(currency))
    _validate_saved_card(user=user, saved_card=saved_card, gateway_name=gateway.name)
    payment = Payment(
        user=user,
        amount=amount,
        currency=currency,
        kind=kind,
        description=description,
        gateway=gateway.name,
        saved_card=saved_card,
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
        saved_card=_card_ref(saved_card),
    )
    try:
        session = gateway.charge_saved(request=request)
    except (OutboundError, GatewayResponseError) as exc:
        payment.status = PaymentStatus.FAILED
        payment.save(update_fields=["status", "updated_at"])
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    payment.gateway_charge_id = session.charge_id
    payment.gateway_response = session.raw
    payment.full_clean()
    payment.save(update_fields=["gateway_charge_id", "gateway_response", "updated_at"])
    if session.status:
        return payment_apply_gateway_event(
            gateway_name=gateway.name, event=_session_event(payment, session)
        )
    return payment  # stays PENDING - the webhook/reconcile sweep settles it


#: Nominal amount for card-setup verifications: Tap authorizes-then-voids
#: it, a Paymob Verification intention never moves it.
_CARD_SETUP_AMOUNT = Decimal("1.00")


def payment_setup_card(*, user: User, card_token: str = "") -> Payment:
    """Add a card without paying: a nominal hosted verification whose only
    purpose is vaulting the card (Tap: authorize + auto-void; Paymob:
    Verification-integration intention).

    Currency - and therefore gateway - comes from the user's wallet; the
    client contract is checkout's: redirect to ``checkout_url``, the gateway
    callback vaults the card (``save_card_requested`` gates persistence
    exactly like an opt-in checkout), and the card shows up in GET /cards.

    ``card_token`` (one-time token from the gateway's card component
    embedded in our frontend) skips the hosted card-entry page: the
    remaining ``checkout_url`` is just the 3DS challenge, and a
    frictionless outcome may settle synchronously (empty ``checkout_url``,
    terminal ``status``).
    """
    wallet = wallet_get(user=user)
    gateway = gateway_for_currency(wallet.currency)
    payment = Payment(
        user=user,
        amount=_CARD_SETUP_AMOUNT,
        currency=wallet.currency,
        kind=PaymentKind.CARD_VERIFICATION,
        description=str(_("Card verification")),
        gateway=gateway.name,
        save_card_requested=True,
    )
    payment.full_clean()
    payment.save()
    request = CheckoutRequest(
        reference=str(payment.idempotency_key),
        amount=payment.amount,
        currency=payment.currency,
        description=payment.description,
        customer_email=user.email,
        customer_name=user.name,
        customer_phone=str(user.phone) if user.phone else "",
        webhook_url=(
            f"{settings.BACKEND_BASE_URL}/api/v1/payments/webhooks/{gateway.name}"
        ),
        redirect_url=f"{settings.FRONTEND_BASE_URL}/payments/{payment.pk}/return",
        save_card=True,
        card_token=card_token,
    )
    try:
        session = gateway.setup_card(request=request)
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
    if not session.checkout_url and session.status:
        return payment_apply_gateway_event(
            gateway_name=gateway.name, event=_session_event(payment, session)
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
        if event.saved_card is not None and payment.save_card_requested:
            # Consent-gated card persistence; the upsert is idempotent, and a
            # replay may be the FIRST carrier of the card payload when
            # payment_verify settled the row before the webhook arrived.
            payment.saved_card = saved_card_store(
                user=payment.user, gateway=gateway_name, data=event.saved_card
            )
        if payment.status in TERMINAL_STATUSES:  # replay: record, never re-credit
            payment.save(
                update_fields=[
                    "gateway_callback",
                    "gateway_transaction_id",
                    "saved_card",
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
                "saved_card",
                "updated_at",
            ]
        )
        if payment.status == PaymentStatus.PAID:
            _on_paid(payment)
        return payment


def _on_paid(payment: Payment) -> None:
    if payment.kind == PaymentKind.CARD_VERIFICATION:
        # Nominal hold/verification - no money to credit, and the card
        # appearing in the user's list is the success signal.
        return
    if payment.kind == PaymentKind.WALLET_TOPUP:
        wallet = _wallet_for(user=payment.user, currency=payment.currency)
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
        saved_card=status.saved_card,
    )
    return payment_apply_gateway_event(gateway_name=payment.gateway, event=event)


def payment_refund_start(*, payment: Payment, actor: User) -> Payment:
    """Refund phase 1, the interlock - the provider is NOT called here.

    Full refund only (partial refunds: extend with an amount arg + partial
    ledger). Locks the row, requires PAID (a concurrent second refund fails
    this check before ever reaching the gateway), debits the wallet for
    top-ups (a spent credit raises InsufficientBalanceError before the
    provider is contacted), flips to REFUND_PENDING, and enqueues
    ``process_payment_refund`` on commit.

    The provider call lives in the worker task on purpose: request handlers
    run under ATOMIC_REQUESTS, which would turn the executor's transactions
    into savepoints - holding the Payment+Wallet row locks across outbound
    HTTP and rolling the interlock back to PAID on a crash even after the
    provider refunded. Committing REFUND_PENDING with the request and doing
    the rest in the worker closes both holes.
    """
    if gateway_by_name(payment.gateway) is None:
        raise PaymentNotFoundError(str(_("Payment gateway is not configured.")))
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if locked.status != PaymentStatus.PAID:
            raise PaymentNotRefundableError(
                str(_("Only paid payments can be refunded."))
            )
        if locked.kind == PaymentKind.WALLET_TOPUP:
            wallet = _wallet_for(user=locked.user, currency=locked.currency)
            wallet_apply(
                wallet_id=wallet.pk,
                amount=-locked.amount,
                kind=WalletTransactionKind.REFUND,
                payment=locked,
                actor=actor,
            )
        locked.status = PaymentStatus.REFUND_PENDING
        locked.refund_attempted_at = None
        locked.full_clean()
        locked.save(update_fields=["status", "refund_attempted_at", "updated_at"])
        transaction.on_commit(
            lambda: process_payment_refund.enqueue(
                payment_id=str(locked.pk), actor_id=str(actor.pk)
            )
        )
        return locked


def payment_refund_execute(
    *, payment_id: uuid.UUID, actor: User | None = None
) -> Payment:
    """Refund phases 2+3: provider call + finalization. Worker/sweep only -
    never call from a request handler (ATOMIC_REQUESTS would reopen the
    crash window the start/execute split exists to close).

    Safe to re-run: a row no longer REFUND_PENDING is a no-op, and a row
    whose ``refund_attempted_at`` marker is already set is never re-sent to
    the provider (``gateway.refund`` is not idempotent at Tap/Paymob) - it
    is logged for manual reconciliation instead.

    Error contract: ``PaymentGatewayUnavailableError`` /
    ``PaymentRefundFailedError`` mean the interlock was reverted (row back
    to PAID, wallet compensated - safe to retry). A raw ``OutboundError`` /
    ``GatewayResponseError`` escaping means the provider MAY have processed
    the refund: the row stays REFUND_PENDING with the marker set, the task
    fails loudly, and a human reconciles against the provider dashboard.
    """
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if locked.status != PaymentStatus.REFUND_PENDING:
            return locked  # replayed task after finalization - nothing to do
        gateway = gateway_by_name(locked.gateway)
        if gateway is None:
            raise PaymentNotFoundError(str(_("Payment gateway is not configured.")))
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
            transaction_id=locked.gateway_transaction_id or locked.gateway_charge_id,
            amount=locked.amount,
            currency=locked.currency,
        )
    except (OutboundError, GatewayResponseError) as exc:
        if _refund_outcome_unknown(exc):
            logger.error(
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
    if not result.ok:
        _refund_revert(payment_id=locked.pk, actor=actor)
        raise PaymentRefundFailedError(str(_("The gateway rejected the refund.")))
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        locked.status = PaymentStatus.REFUNDED
        locked.full_clean()
        locked.save(update_fields=["status", "updated_at"])
        return locked


def _refund_outcome_unknown(exc: Exception) -> bool:
    """Could the provider have processed the refund despite the error?

    Reverting the interlock is only safe when the answer is a hard no -
    otherwise the user could end up refunded at the provider AND re-credited
    in the wallet.
    """
    if isinstance(exc, OutboundTransportError):
        return exc.request_sent
    if isinstance(exc, OutboundStatusError):
        # A 4xx is a definitive rejection; a 5xx may have crashed after
        # processing.
        return exc.status_code is None or exc.status_code >= 500
    return True  # GatewayResponseError: 2xx but unusable body - assume processed


def _refund_revert(*, payment_id: uuid.UUID, actor: User | None) -> None:
    """Undo the refund interlock: compensate the wallet debit, restore PAID."""
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment_id)
        if locked.kind == PaymentKind.WALLET_TOPUP:
            wallet = _wallet_for(user=locked.user, currency=locked.currency)
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
        locked.full_clean()
        locked.save(update_fields=["status", "refund_attempted_at", "updated_at"])
