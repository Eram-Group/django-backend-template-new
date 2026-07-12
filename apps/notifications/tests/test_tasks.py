"""Delivery tasks: idempotency markers, locale rendering, token pruning."""

from typing import Any

import pytest

from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.sms import backends as sms_backends
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Device
from apps.notifications.tasks import send_push_notification
from apps.notifications.tasks import send_sms_notification
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_push_task_delivers_and_stamps_marker() -> None:
    notification = NotificationFactory.create(context={"name": "Omar"})
    DeviceFactory.create(user=notification.recipient)

    send_push_notification.enqueue(str(notification.pk))

    assert len(push_backends.outbox) == 1
    sent = push_backends.outbox[0]
    assert "Omar" in sent.body
    assert sent.data["notification_id"] == str(notification.pk)
    assert sent.data["kind"] == NotificationKind.WELCOME
    notification.refresh_from_db()
    assert notification.push_sent_at is not None


def test_push_task_reruns_are_no_ops() -> None:
    notification = NotificationFactory.create()
    DeviceFactory.create(user=notification.recipient)

    send_push_notification.enqueue(str(notification.pk))
    send_push_notification.enqueue(str(notification.pk))  # manual re-enqueue

    assert len(push_backends.outbox) == 1


def test_push_task_without_devices_still_stamps_marker() -> None:
    notification = NotificationFactory.create()

    send_push_notification.enqueue(str(notification.pk))

    assert not push_backends.outbox
    notification.refresh_from_db()
    assert notification.push_sent_at is not None


def test_push_task_prunes_invalid_tokens(settings: Any) -> None:
    settings.PUSH_BACKEND = "apps.notifications.tests.test_push.InvalidTokenPushBackend"
    notification = NotificationFactory.create()
    device = DeviceFactory.create(user=notification.recipient)

    send_push_notification.enqueue(str(notification.pk))

    assert not Device.objects.filter(pk=device.pk).exists()


def test_sms_task_delivers_to_the_recipient_phone() -> None:
    user = UserFactory.create(phone="+966501234567")
    notification = NotificationFactory.create(
        recipient=user,
        kind=NotificationKind.ANNOUNCEMENT,
        context={"message": "Hello"},
    )

    send_sms_notification.enqueue(str(notification.pk))

    assert len(sms_backends.outbox) == 1
    assert sms_backends.outbox[0].to == "+966501234567"
    notification.refresh_from_db()
    assert notification.sms_sent_at is not None


def test_sms_task_skips_phoneless_users_but_stamps_marker() -> None:
    notification = NotificationFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, context={"message": "Hello"}
    )

    send_sms_notification.enqueue(str(notification.pk))

    assert not sms_backends.outbox
    notification.refresh_from_db()
    assert notification.sms_sent_at is not None


def test_sms_task_reruns_are_no_ops() -> None:
    user = UserFactory.create(phone="+966501234567")
    notification = NotificationFactory.create(
        recipient=user,
        kind=NotificationKind.ANNOUNCEMENT,
        context={"message": "Hello"},
    )

    send_sms_notification.enqueue(str(notification.pk))
    send_sms_notification.enqueue(str(notification.pk))

    assert len(sms_backends.outbox) == 1
