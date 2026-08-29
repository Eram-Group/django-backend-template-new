"""Payment factories - factory_boy structure, mimesis values."""

import uuid
from decimal import Decimal

from factory.declarations import LazyAttribute
from factory.declarations import Sequence
from factory.declarations import SubFactory
from factory.django import DjangoModelFactory

from apps.payments.constants import Currency
from apps.payments.constants import GatewayName
from apps.payments.constants import PaymentKind
from apps.payments.constants import WalletTransactionKind
from apps.payments.models import Payment
from apps.payments.models import SavedCard
from apps.payments.models import Wallet
from apps.payments.models import WalletTransaction
from apps.payments.services import wallet_currency_for
from apps.users.tests.factories import UserFactory


class PaymentFactory(DjangoModelFactory[Payment]):
    class Meta:
        model = Payment
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    kind = PaymentKind.WALLET_TOPUP
    amount = Decimal("50.00")
    currency = Currency.SAR
    # The test FakeGateway answers to Tap's name (test.py maps every
    # currency to it), so factory rows resolve to the fake at runtime.
    gateway = GatewayName.TAP


# Sequences restart at 0 per process, but --reuse-db keeps rows committed by
# session fixtures in earlier runs - the tag keeps (gateway, token) unique
# (DeviceFactory precedent).
_RUN_TAG = uuid.uuid4().hex[:6]


class SavedCardFactory(DjangoModelFactory[SavedCard]):
    class Meta:
        model = SavedCard
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    gateway = GatewayName.TAP
    # Opaque provider ids are not human values, so no mimesis here
    # (PaymentFactory precedent).
    token = Sequence(lambda n: f"fake_card_{_RUN_TAG}_{n}")
    gateway_customer_id = Sequence(lambda n: f"fake_cus_{_RUN_TAG}_{n}")
    gateway_agreement_id = Sequence(lambda n: f"fake_agr_{_RUN_TAG}_{n}")
    fingerprint = Sequence(lambda n: f"fake_fp_{_RUN_TAG}_{n}")
    brand = "VISA"
    last4 = "4242"
    exp_month = 12
    exp_year = 2030


class WalletFactory(DjangoModelFactory[Wallet]):
    class Meta:
        model = Wallet
        django_get_or_create = ["user"]
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    # The same decision signup makes (user_create -> wallet_currency_for).
    currency = LazyAttribute(
        lambda wallet: wallet_currency_for(language=wallet.user.language)
    )


class WalletTransactionFactory(DjangoModelFactory[WalletTransaction]):
    class Meta:
        model = WalletTransaction
        skip_postgeneration_save = True

    wallet = SubFactory(WalletFactory)
    kind = WalletTransactionKind.TOPUP
    amount = Decimal("10.00")
    balance_after = Decimal("10.00")
