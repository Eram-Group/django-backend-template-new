"""Model invariants: idempotency constraints and structural guards."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationDelivery
from apps.notifications.tests.factories import BroadcastFactory
from apps.notifications.tests.factories import NotificationChannelOverrideFactory
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


def test_channel_override_rejects_unsupported_channel() -> None:
    """WELCOME supports push only - pinning whatsapp on it is a config error."""
    override = NotificationChannelOverrideFactory.build(
        kind=NotificationKind.WELCOME, channel=Channel.WHATSAPP, enabled=True
    )

    with pytest.raises(ValidationError, match="does not support"):
        override.full_clean()


def test_channel_override_accepts_supported_channel() -> None:
    override = NotificationChannelOverrideFactory.build(
        kind=NotificationKind.ANNOUNCEMENT, channel=Channel.WHATSAPP, enabled=True
    )

    override.full_clean()  # does not raise
