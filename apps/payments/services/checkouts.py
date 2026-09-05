"""Checkout writes: open a checkout, charge a stored card, verify, expire.

A checkout is one Payment row + one provider session. The PENDING row is
committed BEFORE the provider is contacted (no request-wide transaction), so
a webhook can never race its own row; the provider call itself runs outside
any lock. Its outcome is applied through ``events.payment_apply_gateway_event``
- the same guarded transition a webhook drives - never by writing the row
from here: when the provider call fails, only a provably-unsent request may
mark the row FAILED (a conditional UPDATE), because a lost response may
already have been settled by the webhook underneath us.
"""

import uuid
from decimal import Decimal

import structlog
from django.conf import settings
from django.db import IntegrityError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.http import OutboundError
from apps.payments.constants import PENDING_EXPIRY
from apps.payments.constants import TERMINAL_STATUSES
from apps.payments.constants import Currency
from apps.payments.constants import PaymentKind
from apps.payments.constants import PaymentStatus
from apps.payments.exceptions import CustomerDetailsRequiredError
from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentRequestConflictError
from apps.payments.exceptions import SavedCardGatewayMismatchError
from apps.payments.exceptions import SavedCardNotFoundError
from apps.payments.gateways import gateway_by_name
from apps.payments.gateways import gateway_for_currency
from apps.payments.gateways.base import CheckoutRequest
from apps.payments.gateways.base import CheckoutSession
from apps.payments.gateways.base import GatewayError
from apps.payments.gateways.base import PaymentGateway
from apps.payments.gateways.base import SavedCardRef
from apps.payments.gateways.base import provider_outcome_unknown
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.selectors.saved_cards import saved_card_gateway_customer_id
from apps.payments.services.events import payment_apply_gateway_event
from apps.payments.services.saved_cards import saved_card_ref
from apps.payments.services.wallets import wallet_for_currency
from apps.users.models import User

logger = structlog.get_logger(__name__)

#: A full name has a first and a last part - both gateways bill them.
_NAME_PARTS = 2


def _customer_details(user: User) -> tuple[str, str]:
    """The billing name and phone the gateways require of every checkout -
    refused up front, before a row exists, never padded with placeholders."""
    name = user.name.strip()
    phone = str(user.phone) if user.phone else ""
    if len(name.split()) < _NAME_PARTS or not phone:
        raise CustomerDetailsRequiredError(
            str(_("Add your full name and phone number before paying."))
        )
    return name, phone


def _checkout_request(
    *,
    payment: Payment,
    gateway_name: str,
    customer_name: str,
    customer_phone: str,
    saved_card: SavedCardRef | None,
    customer_id: str,
) -> CheckoutRequest:
    return CheckoutRequest(
        reference=str(payment.idempotency_key),
        amount=payment.amount,
        currency=payment.currency,
        description=payment.description,
        customer_email=payment.user.email,
        customer_name=customer_name,
        customer_phone=customer_phone,
        webhook_url=(
            f"{settings.BACKEND_BASE_URL}/api/v1/payments/webhooks/{gateway_name}"
        ),
        redirect_url=f"{settings.FRONTEND_BASE_URL}/payments/{payment.pk}/return",
        saved_card=saved_card,
        customer_id=customer_id,
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


def _same_operation(
    payment: Payment,
    *,
    amount: Decimal,
    currency: Currency,
    kind: PaymentKind,
    description: str,
    saved_card: SavedCard | None,
) -> bool:
    return (
        payment.amount == amount
        and payment.currency == currency
        and payment.kind == kind
        and payment.description == description
        and payment.saved_card_id == (saved_card.pk if saved_card else None)
    )


def payment_initiate(
    *,
    user: User,
    request_id: uuid.UUID,
    amount: Decimal,
    currency: Currency,
    kind: PaymentKind,
    description: str,
    saved_card: SavedCard | None,
) -> Payment:
    """Create the PENDING row, then ask the gateway for a checkout URL.

    ``request_id`` is the client's key for THIS operation: a repeated call
    with the same key and payload returns the row it already opened (no
    second provider session, no second charge on the saved-card path); the
    same key with a different payload is a 409 - a retry repeats an
    operation, it never changes one. The client waits for checkout_url, so
    the gateway call is synchronous (kernel timeout 10s < gunicorn's 30s).

    Every new-card checkout requests vaulting (saving is not
    client-optional; a stored card cannot be re-saved). ``saved_card`` pays
    one-click WITH a stored card: Paymob still returns a checkout_url
    (hosted CVV entry); Tap charges server-side and may settle
    synchronously - the response is then already terminal with an empty
    checkout_url, or carries a 3DS-challenge URL to redirect to.

    The PENDING row commits before the gateway is contacted (no request-wide
    transaction), so a webhook can never race its own row; a webhook that
    still 404s (a replay for a purged row) is covered by the gateway's
    retry, ``payment_verify``, and the ``reconcile_payments`` sweep.
    """
    existing = Payment.objects.filter(user=user, client_request_id=request_id).first()
    if existing is not None:
        return _replayed(
            existing,
            amount=amount,
            currency=currency,
            kind=kind,
            description=description,
            saved_card=saved_card,
        )
    customer_name, customer_phone = _customer_details(user)
    if kind == PaymentKind.WALLET_TOPUP:
        # Fail before the provider charges: waiting for the credit path to
        # reject the mismatch would strand already-captured money.
        wallet_for_currency(user=user, currency=currency)
    gateway = gateway_for_currency(currency)
    if saved_card is not None:
        _validate_saved_card(
            user=user, saved_card=saved_card, gateway_name=gateway.name
        )
    payment = Payment(
        user=user,
        client_request_id=request_id,
        amount=amount,
        currency=currency,
        kind=kind,
        description=description,
        gateway=gateway.name,
        saved_card=saved_card,
        save_card_requested=saved_card is None,
    )
    payment.full_clean()
    try:
        with transaction.atomic():
            payment.save()
    except IntegrityError:
        # Two concurrent requests with the same key: the loser reads the
        # winner's row and answers exactly as a later retry would.
        existing = Payment.objects.get(user=user, client_request_id=request_id)
        return _replayed(
            existing,
            amount=amount,
            currency=currency,
            kind=kind,
            description=description,
            saved_card=saved_card,
        )
    request = _checkout_request(
        payment=payment,
        gateway_name=gateway.name,
        customer_name=customer_name,
        customer_phone=customer_phone,
        saved_card=saved_card_ref(saved_card) if saved_card is not None else None,
        # A new card is filed under the customer the user's other cards use,
        # so re-entering a card yields the same provider card, not a copy.
        customer_id=(
            saved_card_gateway_customer_id(user=user, gateway=gateway.name)
            if saved_card is None
            else ""
        ),
    )
    session = _open_session(
        payment=payment, gateway=gateway, request=request, saved=False
    )
    return _record_session(payment=payment, gateway=gateway, session=session)


def _replayed(
    existing: Payment,
    *,
    amount: Decimal,
    currency: Currency,
    kind: PaymentKind,
    description: str,
    saved_card: SavedCard | None,
) -> Payment:
    if _same_operation(
        existing,
        amount=amount,
        currency=currency,
        kind=kind,
        description=description,
        saved_card=saved_card,
    ):
        return existing
    raise PaymentRequestConflictError(
        str(_("This request id was already used for a different payment."))
    )


def payment_charge_saved(
    *,
    user: User,
    saved_card: SavedCard,
    amount: Decimal,
    currency: Currency,
    kind: PaymentKind,
    description: str,
) -> Payment:
    """MIT: charge a stored card server-side, no customer present.

    Never auto-retry a failure here - a merchant-initiated charge that ran
    twice is a double-charge, not a retry. The gateways always plant our
    reference AND the webhook URL, so a crash between the provider call and
    the row update self-heals when the webhook lands (the row binds itself
    to the provider's answer); FAILED is non-terminal, so a late CAPTURED
    webhook corrects a wrongly-FAILED row too.
    """
    customer_name, customer_phone = _customer_details(user)
    if kind == PaymentKind.WALLET_TOPUP:
        wallet_for_currency(user=user, currency=currency)
    gateway = gateway_for_currency(currency)
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
    request = _checkout_request(
        payment=payment,
        gateway_name=gateway.name,
        customer_name=customer_name,
        customer_phone=customer_phone,
        saved_card=saved_card_ref(saved_card),
        customer_id="",  # the stored card already carries its customer
    )
    session = _open_session(
        payment=payment, gateway=gateway, request=request, saved=True
    )
    return _record_session(payment=payment, gateway=gateway, session=session)


def _open_session(
    *,
    payment: Payment,
    gateway: PaymentGateway,
    request: CheckoutRequest,
    saved: bool,
) -> CheckoutSession:
    """The provider call, and the ONLY failure handling a checkout does.

    A provably-unsent request (DNS/connect, 4xx) marks the row FAILED with a
    conditional UPDATE - never through the in-memory instance, which the
    webhook may already have settled underneath us. Any other failure (read
    timeout, 5xx, unusable body) leaves the row PENDING: the provider may
    have captured, its webhook binds and settles the row, and the reconcile
    sweep verifies or expires it. Either way the caller gets a 503.
    """
    try:
        return (
            gateway.charge_saved(request=request)
            if saved
            else gateway.create_checkout(request=request)
        )
    except (OutboundError, GatewayError) as exc:
        if provider_outcome_unknown(exc):
            logger.warning(
                "payment_checkout_outcome_unknown",
                payment_id=str(payment.pk),
                gateway=gateway.name,
                error=type(exc).__name__,
            )
        else:
            Payment.objects.filter(pk=payment.pk, status=PaymentStatus.PENDING).update(
                status=PaymentStatus.FAILED, updated_at=timezone.now()
            )
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc


def _record_session(
    *, payment: Payment, gateway: PaymentGateway, session: CheckoutSession
) -> Payment:
    """Stamp the provider identity on the row, then apply a synchronous
    outcome through the same transition a webhook takes (the webhook that
    follows is an idempotent replay)."""
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
    if session.outcome is not None:
        return payment_apply_gateway_event(
            gateway_name=gateway.name, event=session.outcome
        )
    return payment  # stays PENDING - the webhook/reconcile sweep settles it


def payment_verify(*, payment: Payment) -> Payment:
    """Re-query the gateway on demand (webhook fallback, user is polling).

    Only a PAID answer is applied: a declined attempt on a hosted page can
    still be retried by the customer, so the row stays PENDING until the
    webhook or the reconcile sweep settles it. A row that never learned its
    provider identity (checkout response lost) is left for the webhook to
    bind or the sweep to expire.
    """
    if payment.status in TERMINAL_STATUSES:
        return payment
    gateway = gateway_by_name(payment.gateway)
    try:
        event = gateway.fetch_status(
            charge_id=payment.gateway_charge_id,
            reference=str(payment.idempotency_key),
        )
    except (OutboundError, GatewayError) as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment provider is unavailable. Try again shortly."))
        ) from exc
    if event is None or not event.is_paid:
        return payment
    return payment_apply_gateway_event(gateway_name=payment.gateway, event=event)


def payment_expire(*, payment: Payment) -> Payment:
    """Abandoned checkout: a PENDING row older than ``PENDING_EXPIRY`` becomes
    FAILED. Called by the reconcile sweep after ``payment_verify`` found
    nothing paid, so the sweep's oldest-first window is not clogged forever
    by checkouts nobody completed. FAILED is non-terminal - a late webhook
    still heals a wrongly-expired row.
    """
    with transaction.atomic():
        locked = Payment.objects.select_for_update().get(pk=payment.pk)
        if (
            locked.status != PaymentStatus.PENDING
            or locked.created_at > timezone.now() - PENDING_EXPIRY
        ):
            return locked
        locked.status = PaymentStatus.FAILED
        locked.full_clean()
        locked.save(update_fields=["status", "updated_at"])
        logger.info(
            "payment_expired",
            payment_id=str(locked.pk),
            gateway=locked.gateway,
            created_at=locked.created_at.isoformat(),
        )
        return locked
