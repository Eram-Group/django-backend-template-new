"""Gateway-event transitions: the ONE way a Payment row changes state.

The webhook is the source of truth for payment state; a synchronous charge
outcome and ``payment_verify`` feed the same transition. Every transition
runs under select_for_update on the Payment row and is idempotent against
replayed events (TERMINAL_STATUSES are never overwritten - a second "paid"
webhook cannot credit the wallet twice; the ledger's TOPUP uniqueness is the
DB backstop for that guard).

Two proofs gate every transition. The signature proves the gateway sent the
event; ``_check_event_matches`` proves it is about THIS row: the signed
provider identity (Tap charge id / Paymob order id) must equal the row's
``gateway_charge_id`` and the signed amount/currency must equal the row's.
The merchant reference the row is looked up by is outside both providers'
signatures, so on its own it proves nothing.
"""

import structlog
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.http import OutboundError
from apps.notifications.constants import NotificationKind
from apps.notifications.services import notification_send
from apps.payments.constants import TERMINAL_STATUSES
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.constants import WalletTransactionKind
from apps.payments.exceptions import PaymentEventMismatchError
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentNotFoundError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways.base import GatewayError
from apps.payments.gateways.base import PaymentEvent
from apps.payments.gateways.base import to_minor_units
from apps.payments.models import Payment
from apps.payments.services.saved_cards import saved_card_store
from apps.payments.services.wallets import wallet_apply
from apps.payments.services.wallets import wallet_for_currency

logger = structlog.get_logger(__name__)

#: Informational event statuses that mean the PROVIDER moved money on a row
#: we still hold as PAID (a refund/void issued from the gateway dashboard) -
#: never auto-applied to the wallet; a human reconciles.
_PROVIDER_REVERSALS = frozenset({"refund", "void", "refunded", "voided"})


def payment_apply_gateway_event(*, gateway_name: str, event: PaymentEvent) -> Payment:
    """Apply one gateway event (webhook, sync outcome or verify) - idempotent
    on replays.

    A verified event whose identity, amount or currency disagree with the
    row is never applied (``PaymentEventMismatchError``); an informational
    event (``is_pending`` - still in flight, or a refund/void/capture child)
    is recorded on the row but never transitions it.
    """
    payment = _payment_for(gateway_name=gateway_name, reference=event.reference)
    if not payment.gateway_charge_id:
        # The checkout response was lost before the row learned its provider
        # identity: the webhook cannot be bound, so the provider's own
        # answer (an authenticated lookup, outside any lock) becomes the
        # event - it carries the identity the row then keeps.
        event = _bind_from_provider(payment=payment, event=event)
    payment = _apply_transition(gateway_name=gateway_name, event=event)
    if event.saved_card is not None and payment.save_card_requested:
        # Consent-gated card persistence, AFTER the row lock is released: the
        # upsert reads the provider vault (HTTP) and must not hold the
        # Payment row. It is idempotent, and a replay may be the FIRST
        # carrier of the card payload when payment_verify settled the row
        # before the webhook arrived - a crash between the two writes heals
        # on that replay.
        card = saved_card_store(
            user=payment.user, gateway=gateway_name, data=event.saved_card
        )
        payment.saved_card = card
        Payment.objects.filter(pk=payment.pk).update(
            saved_card=card, updated_at=timezone.now()
        )
    return payment


def _payment_for(*, gateway_name: str, reference: str) -> Payment:
    try:
        return Payment.objects.get(idempotency_key=reference, gateway=gateway_name)
    except (Payment.DoesNotExist, ValueError, ValidationError) as exc:
        raise PaymentNotFoundError(str(_("Payment not found."))) from exc


def _bind_from_provider(*, payment: Payment, event: PaymentEvent) -> PaymentEvent:
    """Recover a row's provider identity from the provider itself.

    Asks the gateway for the object the event names (Tap: the charge by its
    signed id; Paymob: the order by our reference) and accepts the answer
    only when the provider echoes OUR reference under THAT identity - the
    unsigned reference on the webhook is never trusted to do the binding.
    The identity is stamped with a conditional UPDATE so a racing bind
    cannot overwrite it.
    """
    gateway = gateway_by_name(payment.gateway)
    reference = str(payment.idempotency_key)
    try:
        fetched = gateway.fetch_status(charge_id=event.charge_id, reference=reference)
    except (OutboundError, GatewayError) as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    if (
        fetched is None
        or fetched.reference != reference
        or (fetched.charge_id != event.charge_id)
    ):
        logger.error(
            "payment_event_identity_unbound",
            payment_id=str(payment.pk),
            gateway=payment.gateway,
            event_charge_id=event.charge_id,
            fetched_charge_id=None if fetched is None else fetched.charge_id,
        )
        raise PaymentEventMismatchError(
            str(_("The gateway event does not match this payment."))
        )
    Payment.objects.filter(pk=payment.pk, gateway_charge_id="").update(
        gateway_charge_id=fetched.charge_id, updated_at=timezone.now()
    )
    return fetched


def _apply_transition(*, gateway_name: str, event: PaymentEvent) -> Payment:
    with transaction.atomic():
        try:
            payment = Payment.objects.select_for_update().get(
                idempotency_key=event.reference, gateway=gateway_name
            )
        except (Payment.DoesNotExist, ValueError, ValidationError) as exc:
            raise PaymentNotFoundError(str(_("Payment not found."))) from exc
        _check_event_matches(payment=payment, event=event)
        payment.gateway_callback = event.raw
        if payment.status in TERMINAL_STATUSES or event.is_pending:
            # Replay or informational event: record, never (re-)credit.
            if event.transaction_id:  # "" = keep the settled id (child actions)
                _keep_settled_transaction(
                    payment=payment, transaction_id=event.transaction_id
                )
            if event.status in _PROVIDER_REVERSALS and (
                payment.status == PaymentStatus.PAID
            ):
                logger.error(
                    "payment_provider_action_needs_reconciliation",
                    payment_id=str(payment.pk),
                    gateway=gateway_name,
                    action=event.status,
                )
            payment.save(
                update_fields=[
                    "gateway_callback",
                    "gateway_transaction_id",
                    "updated_at",
                ]
            )
            return payment
        # A settling event names the attempt that settles the row - a retry
        # after a declined attempt legitimately carries a new transaction.
        if event.transaction_id:
            payment.gateway_transaction_id = event.transaction_id
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


def _keep_settled_transaction(*, payment: Payment, transaction_id: str) -> None:
    """A settled row keeps its transaction id: a later event naming a
    different one is a second settlement of the same order (or a replayed
    receipt re-addressed to this row) and is refused, never recorded."""
    if payment.status not in TERMINAL_STATUSES or not payment.gateway_transaction_id:
        payment.gateway_transaction_id = transaction_id
        return
    if payment.gateway_transaction_id != transaction_id:
        logger.error(
            "payment_event_transaction_mismatch",
            payment_id=str(payment.pk),
            gateway=payment.gateway,
            settled_transaction_id=payment.gateway_transaction_id,
            event_transaction_id=transaction_id,
        )
        raise PaymentEventMismatchError(
            str(_("The gateway event does not match this payment."))
        )


def _check_event_matches(*, payment: Payment, event: PaymentEvent) -> None:
    """Cross-check the gateway's signed identity and amount/currency against
    the row.

    The signature proves the gateway sent it; this proves it is about THIS
    payment at THIS price. The identity check applies to every event, the
    amount check only to settling ones: informational events never move
    money, and a refund/void/capture child carries its own amount (a
    partial refund's), not the payment's.
    """
    if event.charge_id != payment.gateway_charge_id:
        logger.error(
            "payment_event_identity_mismatch",
            payment_id=str(payment.pk),
            gateway=payment.gateway,
            expected_charge_id=payment.gateway_charge_id,
            event_charge_id=event.charge_id,
        )
        raise PaymentEventMismatchError(
            str(_("The gateway event does not match this payment."))
        )
    if event.is_pending:
        return
    expected = to_minor_units(amount=payment.amount)
    if event.amount_minor == expected and event.currency == payment.currency:
        return
    logger.error(
        "payment_event_amount_mismatch",
        payment_id=str(payment.pk),
        gateway=payment.gateway,
        transaction_id=event.transaction_id,
        expected_minor=expected,
        expected_currency=payment.currency,
        event_minor=event.amount_minor,
        event_currency=event.currency,
    )
    raise PaymentEventMismatchError(
        str(_("The gateway event does not match this payment."))
    )


def _on_paid(payment: Payment) -> None:
    """Post-transition effects of a payment reaching PAID.

    Announcing the outcome goes through notifications' service re-export -
    an explicit cross-app call recorded in pyproject ignore_imports.
    """
    if payment.kind == PaymentKind.WALLET_TOPUP:
        wallet = wallet_for_currency(user=payment.user, currency=payment.currency)
        entry = wallet_apply(
            wallet_id=wallet.pk,
            amount=payment.amount,
            kind=WalletTransactionKind.TOPUP,
            payment=payment,
            actor=None,
            note="",
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
