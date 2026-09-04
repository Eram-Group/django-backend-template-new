from apps.common.exceptions import ApplicationError


class PaymentError(ApplicationError):
    """Base error for the payments domain."""


class PaymentNotFoundError(PaymentError):
    status_code = 404


class PaymentGatewayUnknownError(PaymentError):
    """No gateway carries that name - a wrong URL segment, not an outage."""

    status_code = 404


class PaymentGatewayUnavailableError(PaymentError):
    """A mapped gateway refused to build (missing settings) or is down."""

    status_code = 503


class CustomerDetailsRequiredError(PaymentError):
    """Checkout needs the customer's full name and phone: the gateways bill
    them and we never send placeholders in their place."""


class PaymentNotRefundableError(PaymentError):
    pass


class PaymentRefundFailedError(PaymentError):
    pass


class WebhookRejectedError(PaymentError):
    """Webhook signature verification failed - the gateway should retry/alert."""


class PaymentEventMismatchError(PaymentError):
    """A verified gateway event carries an amount/currency that is not the
    Payment row's - never applied; logged for reconciliation."""


class SavedCardNotFoundError(PaymentError):
    status_code = 404


class SavedCardGatewayMismatchError(PaymentError):
    """The card's gateway does not serve the payment's currency."""


class WalletError(ApplicationError):
    """Base error for wallet operations."""


class WalletNotFoundError(WalletError):
    """Every user gets a wallet at signup - missing means broken invariant."""

    status_code = 404


class InsufficientBalanceError(WalletError):
    pass


class WalletCurrencyMismatchError(WalletError):
    pass
