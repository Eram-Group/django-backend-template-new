"""Explicit model -> factory registry (no auto-discovery, loud failures).

Every concrete model in apps/ MUST be registered here; the factory-coverage
gate (test_factory_coverage.py) fails the suite for any missing entry, and
seed_db populates the database through this mapping.
"""

from typing import Any

from django.db.models import Model
from factory.django import DjangoModelFactory

from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.payments.models import Payment
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.payments.tests.factories import PaymentFactory
from apps.payments.tests.factories import WalletFactory
from apps.payments.tests.factories import WalletTransactionFactory
from apps.users.models import User
from apps.users.tests.factories import UserFactory

FACTORIES: dict[type[Model], type[DjangoModelFactory[Any]]] = {
    User: UserFactory,
    Device: DeviceFactory,
    Notification: NotificationFactory,
    Payment: PaymentFactory,
    Wallet: WalletFactory,
    WalletTransaction: WalletTransactionFactory,
}


def factory_for(model: type[Model]) -> type[DjangoModelFactory[Any]]:
    try:
        return FACTORIES[model]
    except KeyError as exc:
        msg = (
            f"No factory registered for {model._meta.label}. Add one to "
            "apps/<app>/tests/factories.py and register it in "
            "apps.common.tests.factories_registry.FACTORIES."
        )
        raise LookupError(msg) from exc
