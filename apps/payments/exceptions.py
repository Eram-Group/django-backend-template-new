from apps.common.exceptions import ApplicationError


class PaymentError(ApplicationError):
    """Base error for the payments domain."""


class PaymentNotFoundError(PaymentError):
    status_code = 404


class PaymentGatewayUnavailableError(PaymentError):
    status_code = 503


class PaymentNotRefundableError(PaymentError):
    pass


class PaymentRefundFailedError(PaymentError):
    pass


class WebhookRejectedError(PaymentError):
    """Webhook signature verification failed - the gateway should retry/alert."""


class WalletError(ApplicationError):
    """Base error for wallet operations."""


class InsufficientBalanceError(WalletError):
    pass


class WalletCurrencyMismatchError(WalletError):
    pass
