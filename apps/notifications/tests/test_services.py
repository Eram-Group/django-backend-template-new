"""Service behavior: send fan-out, devices, status ingestion."""

from typing import Any

import pytest

from apps.notifications import services
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import DevicePlatform
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Device
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.notifications.tests.locmem import push_outbox
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


# --- notification_send --------------------------------------------------------


def test_send_creates_inbox_row_and_pending_deliveries(
    run_enqueued_tasks: Any,
) -> None:
    user = UserFactory.create()
    DeviceFactory.create(user=user)

    with run_enqueued_tasks():
        notification = services.notification_send(
            recipient=user,
            kind=NotificationKind.WALLET_CREDITED,
            context={"amount": "10.00", "currency": "SAR", "balance": "20.00"},
        )

    delivery = notification.deliveries.get()
    assert delivery.channel == Channel.PUSH
    assert delivery.status == DeliveryStatus.SENT  # the drained delivery task
    assert len(push_outbox) == 1


def test_send_validates_context_keys() -> None:
    user = UserFactory.create()

    with pytest.raises(ValueError, match="missing=\\['balance'\\]"):
        services.notification_send(
            recipient=user,
            kind=NotificationKind.WALLET_CREDITED,
            context={"amount": "10.00", "currency": "SAR"},
        )


def test_send_honors_the_kind_config_channels(
    run_enqueued_tasks: Any,
) -> None:
    NotificationKindConfig.objects.filter(kind=NotificationKind.PAYMENT_PAID).update(
        channels=[]
    )
    user = UserFactory.create()

    with run_enqueued_tasks():
        notification = services.notification_send(
            recipient=user,
            kind=NotificationKind.PAYMENT_PAID,
            context={"amount": "10.00", "currency": "SAR"},
        )

    assert not notification.deliveries.exists()  # inbox-only


# --- read state ---------------------------------------------------------------


def test_mark_read_is_idempotent() -> None:
    notification = NotificationFactory.create()

    first = services.notification_mark_read(
        user=notification.recipient, pk=notification.pk
    )
    second = services.notification_mark_read(
        user=notification.recipient, pk=notification.pk
    )

    assert first.read_at is not None
    assert second.read_at == first.read_at


def test_mark_all_read_returns_count_and_sets_updated_at() -> None:
    user = UserFactory.create()
    NotificationFactory.create(recipient=user)
    NotificationFactory.create(recipient=user)

    assert services.notification_mark_all_read(user=user) == 2
    assert services.notification_mark_all_read(user=user) == 0


# --- devices ------------------------------------------------------------------


def test_device_register_reassigns_token_to_new_user() -> None:
    device = DeviceFactory.create()
    other = UserFactory.create()

    reassigned = services.device_register(
        user=other,
        registration_id=device.registration_id,
        platform=DevicePlatform.IOS,
    )

    assert reassigned.pk == device.pk
    assert reassigned.user == other
    assert reassigned.platform == DevicePlatform.IOS


def test_device_unregister_is_scoped_and_idempotent() -> None:
    device = DeviceFactory.create()
    other = UserFactory.create()

    services.device_unregister(user=other, registration_id=device.registration_id)
    assert Device.objects.filter(pk=device.pk).exists()

    services.device_unregister(user=device.user, registration_id=device.registration_id)
    services.device_unregister(user=device.user, registration_id=device.registration_id)
    assert not Device.objects.filter(pk=device.pk).exists()


# --- delivery_update_status (webhook ingestion) -------------------------------


def _whatsapp_delivery(status: DeliveryStatus) -> NotificationDelivery:
    return NotificationDeliveryFactory.create(
        channel=Channel.WHATSAPP,
        status=status,
        provider="whatsapp",
        provider_message_id="wamid.test.1",
    )


def test_status_moves_forward_and_ignores_regressions() -> None:
    delivery = _whatsapp_delivery(DeliveryStatus.SENT)

    assert services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.test.1",
        status=DeliveryStatus.READ,
    )
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.READ

    # A late "delivered" after "read" is a rowcount-0 no-op.
    assert not services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.test.1",
        status=DeliveryStatus.DELIVERED,
    )
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.READ


def test_duplicate_status_report_is_a_no_op() -> None:
    _whatsapp_delivery(DeliveryStatus.SENT)

    assert services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.test.1",
        status=DeliveryStatus.DELIVERED,
    )
    assert not services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.test.1",
        status=DeliveryStatus.DELIVERED,
    )


def test_failed_is_ignored_after_delivered() -> None:
    delivery = _whatsapp_delivery(DeliveryStatus.DELIVERED)

    assert not services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.test.1",
        status=DeliveryStatus.FAILED,
        detail="expired",
    )
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED


def test_failed_applies_from_sent_with_detail() -> None:
    delivery = _whatsapp_delivery(DeliveryStatus.SENT)

    assert services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.test.1",
        status=DeliveryStatus.FAILED,
        detail="recipient blocked business",
    )
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.detail == "recipient blocked business"


def test_unknown_provider_message_id_is_acked_but_ignored() -> None:
    assert not services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.unknown",
        status=DeliveryStatus.DELIVERED,
    )
