"""The gateway registry - one, built from ``settings.PAYMENT_GATEWAYS``.

``settings.PAYMENT_GATEWAYS`` maps currency -> dotted gateway class; base
settings map Tap (SAR) / Paymob (EGP) in every environment (the ``.env``
keys pick test vs live mode), and test.py pins every currency to the test
FakeGateway so suites never do provider HTTP. Adding a gateway = one module
implementing PaymentGateway + one mapping entry.

The registry holds classes, not instances: a constructor reads the
gateway's settings and refuses to build when one is missing, and that must
surface on the request that needs the gateway (as a 503), not at import.
An unknown name is ``PaymentGatewayUnknownError`` (404); a mapped gateway
that refuses to build, or an unmapped currency, is
``PaymentGatewayUnavailableError`` (503) - callers never see ``None``.
"""

from django.conf import settings
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.exceptions import PaymentGatewayUnknownError
from apps.payments.gateways.base import GatewayConfigurationError
from apps.payments.gateways.base import PaymentGateway


def gateway_classes() -> dict[str, type[PaymentGateway]]:
    """``name`` -> class, read from settings on every call (a module-level
    registry would freeze the setting at import and ignore override_settings);
    ``import_string`` is a sys.modules lookup once the module is loaded."""
    return {
        gateway_cls.name: gateway_cls
        for gateway_cls in map(
            import_string, dict.fromkeys(settings.PAYMENT_GATEWAYS.values())
        )
    }


def gateway_by_name(name: str) -> PaymentGateway:
    """Resolve a configured gateway by its ``name`` (Payment.gateway, the
    webhook URL segment)."""
    try:
        gateway_cls = gateway_classes()[name]
    except KeyError as exc:
        raise PaymentGatewayUnknownError(str(_("Unknown payment gateway."))) from exc
    try:
        return gateway_cls()
    except GatewayConfigurationError as exc:
        raise PaymentGatewayUnavailableError(
            str(_("The payment gateway is not configured."))
        ) from exc


def gateway_for_currency(currency: str) -> PaymentGateway:
    try:
        path = settings.PAYMENT_GATEWAYS[currency]
    except KeyError as exc:
        raise PaymentGatewayUnavailableError(
            str(_("No payment gateway is configured for this currency."))
        ) from exc
    return gateway_by_name(import_string(path).name)
