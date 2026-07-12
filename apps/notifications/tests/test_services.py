"""Services: fan-out policy, context validation, inbox writes, device lifecycle."""

from typing import Any

import pytest

from apps.notifications import services
from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.sms import backends as sms_backends
from apps.notifications.constants import DevicePlatform
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import NotificationNotFoundError
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_notification_send_creates_row_and_pushes_on_commit(
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create()
    DeviceFactory.create(user=user)

    with django_capture_on_commit_callbacks(execute=True):
        notification = services.notification_send(
            recipient=user, kind=NotificationKind.WELCOME, context={"name": "Omar"}
        )

    assert Notification.objects.filter(pk=notification.pk).exists()
    assert len(push_backends.outbox) == 1  # WELCOME is push-only
    assert not sms_backends.outbox
    assert "Omar" in push_backends.outbox[0].body


def test_notification_send_fans_out_sms_for_sms_kinds(
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create(phone="+966501234567")

    with django_capture_on_commit_callbacks(execute=True):
        services.notification_send(
            recipient=user,
            kind=NotificationKind.ANNOUNCEMENT,
            context={"message": "Maintenance tonight."},
        )

    assert len(sms_backends.outbox) == 1
    assert sms_backends.outbox[0].to == "+966501234567"
    assert "Maintenance tonight." in sms_backends.outbox[0].body


def test_notification_send_without_commit_sends_nothing() -> None:
    user = UserFactory.create()
    DeviceFactory.create(user=user)

    services.notification_send(
        recipient=user, kind=NotificationKind.WELCOME, context={"name": "X"}
    )  # test transaction never commits

    assert not push_backends.outbox


def test_notification_send_rejects_wrong_context_keys() -> None:
    user = UserFactory.create()

    with pytest.raises(ValueError, match="unexpected=\\['nam'\\]"):
        services.notification_send(
            recipient=user, kind=NotificationKind.WELCOME, context={"nam": "typo"}
        )
    assert not Notification.objects.filter(recipient=user).exists()


def test_mark_read_is_scoped_to_the_recipient() -> None:
    notification = NotificationFactory.create()
    stranger = UserFactory.create()

    with pytest.raises(NotificationNotFoundError):
        services.notification_mark_read(user=stranger, pk=notification.pk)

    read = services.notification_mark_read(
        user=notification.recipient, pk=notification.pk
    )
    assert read.read_at is not None


def test_mark_read_twice_keeps_the_first_timestamp() -> None:
    notification = NotificationFactory.create()

    first = services.notification_mark_read(
        user=notification.recipient, pk=notification.pk
    ).read_at
    second = services.notification_mark_read(
        user=notification.recipient, pk=notification.pk
    ).read_at

    assert first == second


def test_mark_all_read_counts_only_unread() -> None:
    user = UserFactory.create()
    NotificationFactory.create_batch(3, recipient=user)
    services.notification_mark_read(
        user=user, pk=NotificationFactory.create(recipient=user).pk
    )

    assert services.notification_mark_all_read(user=user) == 3
    assert services.notification_mark_all_read(user=user) == 0


def test_device_register_is_an_upsert_that_reassigns() -> None:
    first_owner = UserFactory.create()
    second_owner = UserFactory.create()

    device = services.device_register(
        user=first_owner, registration_id="tok-1", platform=DevicePlatform.ANDROID
    )
    reassigned = services.device_register(
        user=second_owner, registration_id="tok-1", platform=DevicePlatform.IOS
    )

    assert device.pk == reassigned.pk
    assert reassigned.user == second_owner
    assert reassigned.platform == DevicePlatform.IOS
    assert Device.objects.filter(registration_id="tok-1").count() == 1


def test_device_unregister_is_scoped_and_idempotent() -> None:
    device = DeviceFactory.create()
    stranger = UserFactory.create()

    services.device_unregister(
        user=stranger, registration_id=device.registration_id
    )  # someone else's token: no-op
    assert Device.objects.filter(pk=device.pk).exists()

    services.device_unregister(user=device.user, registration_id=device.registration_id)
    services.device_unregister(  # idempotent re-run
        user=device.user, registration_id=device.registration_id
    )
    assert not Device.objects.filter(pk=device.pk).exists()
