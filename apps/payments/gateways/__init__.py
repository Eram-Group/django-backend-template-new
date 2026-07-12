"""Gateway resolution - the EMAIL_BACKEND pattern, keyed by currency.

``settings.PAYMENT_GATEWAYS`` maps currency -> dotted gateway class; base
and test settings point every currency at the FakeGateway, production.py
swaps in Tap (SAR) / Paymob (EGP) when deployed. Adding a gateway = one
module implementing PaymentGateway + one mapping entry.
"""

from django.conf import settings
from django.utils.module_loading import import_string

from apps.payments.gateways.base import PaymentGateway


def gateway_for_currency(currency: str) -> PaymentGateway:
    gateway: PaymentGateway = import_string(settings.PAYMENT_GATEWAYS[currency])()
    return gateway


def gateway_by_name(name: str) -> PaymentGateway | None:
    """Resolve a configured gateway by its ``name`` (webhook URL segment)."""
    for path in set(settings.PAYMENT_GATEWAYS.values()):
        gateway: PaymentGateway = import_string(path)()
        if gateway.name == name:
            return gateway
    return None
