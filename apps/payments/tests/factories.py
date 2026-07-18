"""Payment factories - factory_boy structure, mimesis values."""

from decimal import Decimal

from factory.declarations import SubFactory
from factory.django import DjangoModelFactory

from apps.payments.constants import DEFAULT_CURRENCY
from apps.payments.constants import Currency
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import WalletTransactionKind
from apps.payments.models import Payment
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.users.tests.factories import UserFactory


class PaymentFactory(DjangoModelFactory[Payment]):
    class Meta:
        model = Payment
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    kind = PaymentKind.WALLET_TOPUP
    amount = Decimal("50.00")
    currency = Currency.SAR
    gateway = GatewayName.FAKE


class WalletFactory(DjangoModelFactory[Wallet]):
    class Meta:
        model = Wallet
        django_get_or_create = ["user"]
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    currency = DEFAULT_CURRENCY  # same default the signup provisioning uses


class WalletTransactionFactory(DjangoModelFactory[WalletTransaction]):
    class Meta:
        model = WalletTransaction
        skip_postgeneration_save = True

    wallet = SubFactory(WalletFactory)
    kind = WalletTransactionKind.TOPUP
    amount = Decimal("10.00")
    balance_after = Decimal("10.00")
