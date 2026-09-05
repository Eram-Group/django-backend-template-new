"""Notification factories - factory_boy structure, mimesis values."""

from factory.declarations import LazyAttribute
from factory.declarations import Sequence
from factory.declarations import SubFactory
from factory.django import DjangoModelFactory

from apps.common.tests import fake
from apps.notifications.catalog import kind_config_seed as _kind_config_seed
from apps.notifications.constants import Channel
from apps.notifications.constants import DevicePlatform
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig
from apps.users.tests.factories import UserFactory


def _context_for(kind: str, *, name: str) -> dict[str, str]:
    """A context satisfying the catalog's context_keys for the kind."""
    contexts: dict[str, dict[str, str]] = {
        NotificationKind.WELCOME: {"name": name},
        NotificationKind.ANNOUNCEMENT: {
            "title": "Maintenance",
            "message": "Scheduled maintenance tonight.",
        },
        NotificationKind.PAYMENT_PAID: {"amount": "100.00", "currency": "SAR"},
        NotificationKind.WALLET_CREDITED: {
            "amount": "100.00",
            "currency": "SAR",
            "balance": "250.00",
        },
    }
    return contexts[NotificationKind(kind)]


class DeviceFactory(DjangoModelFactory[Device]):
    class Meta:
        model = Device
        skip_postgeneration_save = True

    user = SubFactory(UserFactory)
    registration_id = Sequence(lambda n: f"fcm-token-{n:08d}")
    platform = DevicePlatform.ANDROID


class NotificationFactory(DjangoModelFactory[Notification]):
    class Meta:
        model = Notification
        skip_postgeneration_save = True

    recipient = SubFactory(UserFactory)
    kind = NotificationKind.WELCOME
    context = LazyAttribute(
        lambda o: _context_for(o.kind, name=fake.full_name(o.recipient.language))
    )


class BroadcastFactory(DjangoModelFactory[Broadcast]):
    class Meta:
        model = Broadcast
        skip_postgeneration_save = True

    kind = NotificationKind.ANNOUNCEMENT
    context = {"title": "Maintenance", "message": "Scheduled maintenance tonight."}
    created_by = SubFactory(UserFactory)
    # Audience default mirrors an unfiltered push send: every active user.
    require_device = False
    channels: list[str] = [str(Channel.PUSH)]


class NotificationDeliveryFactory(DjangoModelFactory[NotificationDelivery]):
    class Meta:
        model = NotificationDelivery
        skip_postgeneration_save = True

    notification = SubFactory(NotificationFactory)
    # Keep the denormalized copy consistent with the parent notification.
    broadcast = LazyAttribute(lambda o: o.notification.broadcast)
    channel = Channel.PUSH


kind_config_seed = _kind_config_seed  # the catalog's one seed, re-exported


def seed_kind_configs() -> None:
    """Every kind's row in its catalog starting state - the test-suite
    equivalent of an operator having saved each card once."""
    for kind in NotificationKind:
        NotificationKindConfig.objects.update_or_create(
            kind=kind, defaults=kind_config_seed(NotificationKind(kind))
        )


class NotificationKindConfigFactory(DjangoModelFactory[NotificationKindConfig]):
    """Exists for the factory-coverage and admin gates.

    Every kind's row already exists (the conftest reset seeds them), so
    get_or_create returns the SEEDED row and ignores other kwargs - tests that
    want a different policy edit the row directly, they don't call this.
    """

    class Meta:
        model = NotificationKindConfig
        django_get_or_create = ["kind"]
        skip_postgeneration_save = True

    kind = NotificationKind.WELCOME
    channels = LazyAttribute(
        lambda o: kind_config_seed(NotificationKind(o.kind))["channels"]
    )
    title = LazyAttribute(lambda o: kind_config_seed(NotificationKind(o.kind))["title"])
    title_ar = LazyAttribute(
        lambda o: kind_config_seed(NotificationKind(o.kind))["title_ar"]
    )
    title_en = LazyAttribute(
        lambda o: kind_config_seed(NotificationKind(o.kind))["title_en"]
    )
    body = LazyAttribute(lambda o: kind_config_seed(NotificationKind(o.kind))["body"])
    body_ar = LazyAttribute(
        lambda o: kind_config_seed(NotificationKind(o.kind))["body_ar"]
    )
    body_en = LazyAttribute(
        lambda o: kind_config_seed(NotificationKind(o.kind))["body_en"]
    )
