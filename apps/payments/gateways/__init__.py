"""Gateway resolution - the mail-backend pattern, keyed by currency.

``settings.PAYMENT_GATEWAYS`` maps currency -> dotted gateway class; base
and test settings point every currency at the FakeGateway, production.py
swaps in Tap (SAR) / Paymob (EGP) when deployed. Adding a gateway = one
module implementing PaymentGateway + one mapping entry.
"""

from django.conf import settings
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _

from apps.payments.exceptions import PaymentGatewayUnavailableError
from apps.payments.gateways.base import PaymentGateway


def gateway_for_currency(currency: str) -> PaymentGateway:
    try:
        path: str = settings.PAYMENT_GATEWAYS[currency]
    except KeyError as exc:
        # A Currency member without a mapping is a config gap - surface it
        # as a clean 503, not an opaque KeyError 500.
        raise PaymentGatewayUnavailableError(
            str(_("No payment gateway is configured for this currency."))
        ) from exc
    gateway: PaymentGateway = import_string(path)()
    return gateway


def gateway_by_name(name: str) -> PaymentGateway | None:
    """Resolve a configured gateway by its ``name`` (webhook URL segment).

    ``name`` is a class attribute, so matching needs no instantiation -
    only the matched gateway is constructed (constructors read secrets).
    """
    for path in set(settings.PAYMENT_GATEWAYS.values()):
        gateway_cls: type[PaymentGateway] = import_string(path)
        if gateway_cls.name == name:
            return gateway_cls()
    return None
