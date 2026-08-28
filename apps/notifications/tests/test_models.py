"""Model invariants: idempotency constraints and structural guards."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig
from apps.notifications.tests.factories import BroadcastFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_delivery_unique_per_notification_and_channel() -> None:
    """THE idempotency key: one delivery row per (notification, channel)."""
    delivery = NotificationDeliveryFactory.create(channel=Channel.PUSH)

    with pytest.raises(IntegrityError):
        NotificationDelivery.objects.create(
            notification=delivery.notification, channel=Channel.PUSH
        )


def test_provider_message_id_unique_when_present() -> None:
    NotificationDeliveryFactory.create(
        channel=Channel.WHATSAPP, provider="whatsapp", provider_message_id="wamid.1"
    )
    other = NotificationDeliveryFactory.create(channel=Channel.SMS)

    with pytest.raises(IntegrityError):
        NotificationDelivery.objects.create(
            notification=other.notification,
            channel=Channel.WHATSAPP,
            provider="whatsapp",
            provider_message_id="wamid.1",
        )


def test_blank_provider_message_ids_do_not_collide() -> None:
    """Push/SMS return no consumable id - blank rows stay unconstrained."""
    first = NotificationDeliveryFactory.create(channel=Channel.PUSH)
    second = NotificationDeliveryFactory.create(channel=Channel.PUSH)

    assert first.provider_message_id == second.provider_message_id == ""


def test_broadcast_recipient_unique_within_a_broadcast() -> None:
    """Dispatch-resume backstop: one inbox row per (broadcast, recipient)."""
    broadcast = BroadcastFactory.create()
    notification = NotificationFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )

    with pytest.raises(IntegrityError):
        NotificationFactory.create(
            kind=NotificationKind.ANNOUNCEMENT,
            broadcast=broadcast,
            recipient=notification.recipient,
        )


def test_single_send_notifications_are_not_constrained_per_recipient() -> None:
    user = UserFactory.create()
    NotificationFactory.create(recipient=user)
    NotificationFactory.create(recipient=user)  # no broadcast -> no constraint


def test_kind_config_rejects_unsupported_channel() -> None:
    """WELCOME supports push only - enabling whatsapp on it is a config error."""
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
    config.channels = [Channel.WHATSAPP]

    with pytest.raises(ValidationError, match="does not support"):
        config.full_clean()


def test_kind_config_rejects_placeholders_outside_the_contract() -> None:
    """Producers pass exactly context_keys - copy cannot reference more, and
    attribute traversals like {name.__class__} never reach str.format."""
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
    config.body_en = "Hi {surname}!"

    with pytest.raises(ValidationError, match="surname"):
        config.full_clean()


def test_kind_config_requires_copy_in_both_languages() -> None:
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
    config.title_ar = ""

    with pytest.raises(ValidationError, match="title_ar"):
        config.full_clean()


def test_kind_config_accepts_supported_channels_and_known_placeholders() -> None:
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WALLET_CREDITED)
    config.channels = [Channel.PUSH, Channel.SMS]
    config.body_en = "New balance: {balance}."
    config.body_ar = "الرصيد الجديد: {balance}."

    config.full_clean()  # does not raise
