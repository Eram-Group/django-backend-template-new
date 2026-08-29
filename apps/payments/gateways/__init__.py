"""The gateway registry - one, built from ``settings.PAYMENT_GATEWAYS``.

``settings.PAYMENT_GATEWAYS`` maps currency -> dotted gateway class; base
settings map Tap (SAR) / Paymob (EGP) in every environment (the ``.env``
keys pick test vs live mode), and test.py pins every currency to the test
FakeGateway so suites never do provider HTTP. Adding a gateway = one module
implementing PaymentGateway + one mapping entry.

The registry holds classes, not instances: a constructor reads the
gateway's settings and refuses to build when one is missing, and that must
surface on the request that needs the gateway (as a 503), not at import.
Both resolvers raise ``PaymentGatewayUnavailableError`` - callers never see
``None``.
"""

from django.conf import settings
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.gateways.base import GatewayConfigurationError
from apps.payments.gateways.base import PaymentGateway

GATEWAY_CLASSES: dict[str, type[PaymentGateway]] = {
    gateway_cls.name: gateway_cls
    for gateway_cls in map(import_string, set(settings.PAYMENT_GATEWAYS.values()))
}
GATEWAY_NAME_BY_CURRENCY: dict[str, str] = {
    currency: import_string(path).name
    for currency, path in settings.PAYMENT_GATEWAYS.items()
}


def gateway_by_name(name: str) -> PaymentGateway:
    """Resolve a configured gateway by its ``name`` (Payment.gateway, the
    webhook URL segment)."""
    try:
        gateway_cls = GATEWAY_CLASSES[name]
    except KeyError as exc:
        raise PaymentGatewayUnavailableError(
            str(_("Unknown payment gateway."))
        ) from exc
    try:
        return gateway_cls()
    except GatewayConfigurationError as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment gateway is not configured."))
        ) from exc


def gateway_for_currency(currency: str) -> PaymentGateway:
    try:
        name = GATEWAY_NAME_BY_CURRENCY[currency]
    except KeyError as exc:
        raise PaymentGatewayUnavailableError(
            str(_("No payment gateway is configured for this currency."))
        ) from exc
    return gateway_by_name(name)
