"""Executor behavior: claim idempotency, per-channel outcomes, error policy."""

from typing import Any

import pytest
from django.utils import translation

from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.sms import backends as sms_backends
from apps.notifications.clients.sms.base import SmsProviderError
from apps.notifications.clients.whatsapp import backends as whatsapp_backends
from apps.notifications.clients.whatsapp.base import WhatsAppNotConfiguredError
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Device
from apps.notifications.selectors import notification_render
from apps.notifications.tasks.delivery import deliver_notifications
from apps.notifications.tasks.delivery import execute_deliveries
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _delivery(
    *, channel: Channel, kind: NotificationKind = NotificationKind.WALLET_CREDITED
) -> Any:
    notification = NotificationFactory.create(kind=kind)
    return NotificationDeliveryFactory.create(
        notification=notification, channel=channel
    )


# --- claim / idempotency ------------------------------------------------------


def test_double_execution_sends_exactly_once() -> None:
    delivery = _delivery(channel=Channel.PUSH)
    DeviceFactory.create(user=delivery.notification.recipient)

    execute_deliveries(delivery_ids=[str(delivery.pk)])
    execute_deliveries(delivery_ids=[str(delivery.pk)])  # claim finds nothing

    assert len(push_backends.outbox) == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.attempts == 1


def test_task_wrapper_runs_the_executor() -> None:
    delivery = _delivery(channel=Channel.PUSH)
    DeviceFactory.create(user=delivery.notification.recipient)

    deliver_notifications.enqueue([str(delivery.pk)])  # inline in tests

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT


# --- push ---------------------------------------------------------------------


def test_push_sends_one_message_per_device_token(settings: Any) -> None:
    delivery = _delivery(channel=Channel.PUSH)
    DeviceFactory.create(user=delivery.notification.recipient)
    DeviceFactory.create(user=delivery.notification.recipient)

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    assert len(push_backends.outbox) == 2
    assert {m.data["notification_id"] for m in push_backends.outbox} == {
        str(delivery.notification_id)
    }
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.sent_at is not None


def test_push_renders_under_recipient_language() -> None:
    """The executor renders under translation.override(recipient.language) -
    asserted against the same render, so it holds with or without compiled
    .mo catalogs (compiled in the image, not the repo)."""
    user = UserFactory.create(language="ar")
    notification = NotificationFactory.create(
        recipient=user,
        kind=NotificationKind.WALLET_CREDITED,
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )
    DeviceFactory.create(user=user)

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    with translation.override("ar"):
        expected = notification_render(
            kind=NotificationKind.WALLET_CREDITED, context=notification.context
        )
    assert push_backends.outbox[0].title == expected.title
    assert push_backends.outbox[0].body == expected.body


def test_push_without_devices_is_skipped_not_sent() -> None:
    delivery = _delivery(channel=Channel.PUSH)

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SKIPPED
    assert delivery.detail == "no devices"
    assert push_backends.outbox == []


def test_push_prunes_invalid_tokens_and_fails_the_row(settings: Any) -> None:
    settings.PUSH_BACKEND = "apps.notifications.tests.test_push.InvalidTokenPushBackend"
    delivery = _delivery(channel=Channel.PUSH)
    device = DeviceFactory.create(user=delivery.notification.recipient)

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    assert not Device.objects.filter(pk=device.pk).exists()
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.detail == "gone"


# --- sms ----------------------------------------------------------------------


def test_sms_delivers_one_entry_per_recipient() -> None:
    user = UserFactory.create(phone="+966501234567")
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.PAYMENT_PAID
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.SMS
    )

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    assert [entry.to for entry in sms_backends.outbox] == ["+966501234567"]
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT


def test_sms_without_phone_is_skipped() -> None:
    delivery = _delivery(channel=Channel.SMS)

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SKIPPED
    assert delivery.detail == "no phone"
    assert sms_backends.outbox == []


# --- whatsapp -----------------------------------------------------------------


def test_whatsapp_sends_template_and_stores_provider_message_id() -> None:
    user = UserFactory.create(phone="+966501234567", language="ar")
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.WHATSAPP
    )

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    sent = whatsapp_backends.outbox[0]
    assert sent.to == "+966501234567"
    assert sent.template_name == "announcement"
    assert sent.language == "ar"
    assert sent.variables == (notification.context["message"],)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    assert delivery.provider == "whatsapp"
    assert delivery.provider_message_id == "locmem-1"


def test_whatsapp_without_phone_is_skipped() -> None:
    delivery = _delivery(channel=Channel.WHATSAPP, kind=NotificationKind.ANNOUNCEMENT)

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SKIPPED


# --- error policy -------------------------------------------------------------


def test_systemic_failure_escapes_and_leaves_rows_processing(
    settings: Any,
) -> None:
    """NotConfigured = systemic: the task fails loudly; claimed rows stay
    PROCESSING so the sweep resets exactly the remainder."""
    settings.WHATSAPP_BACKEND = (
        "apps.notifications.clients.whatsapp.meta.MetaWhatsAppBackend"
    )
    user = UserFactory.create(phone="+966501234567")
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.WHATSAPP
    )

    with pytest.raises(WhatsAppNotConfiguredError):
        execute_deliveries(delivery_ids=[str(delivery.pk)])

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PROCESSING
    assert delivery.attempts == 1


def test_mixed_channel_batch_processes_each_channel() -> None:
    user = UserFactory.create(phone="+966501234567")
    DeviceFactory.create(user=user)
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.PAYMENT_PAID
    )
    push_row = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )
    sms_row = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.SMS
    )

    execute_deliveries(delivery_ids=[str(push_row.pk), str(sms_row.pk)])

    push_row.refresh_from_db()
    sms_row.refresh_from_db()
    assert push_row.status == DeliveryStatus.SENT
    assert sms_row.status == DeliveryStatus.SENT
    assert len(push_backends.outbox) == 1
    assert len(sms_backends.outbox) == 1


# --- outcomes persist per channel ----------------------------------------------


def test_systemic_failure_in_a_later_channel_keeps_earlier_sends(
    settings: Any,
) -> None:
    """Push went out; WhatsApp then raised NotConfigured. The push row must be
    SENT in the database - left PROCESSING, the sweep would reset it and the
    user would receive the same push again on every pass."""
    settings.WHATSAPP_BACKEND = (
        "apps.notifications.clients.whatsapp.meta.MetaWhatsAppBackend"
    )
    user = UserFactory.create(phone="+966501234567")
    DeviceFactory.create(user=user)
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT
    )
    push_row = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )
    whatsapp_row = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.WHATSAPP
    )

    with pytest.raises(WhatsAppNotConfiguredError):
        execute_deliveries(delivery_ids=[str(push_row.pk), str(whatsapp_row.pk)])

    push_row.refresh_from_db()
    whatsapp_row.refresh_from_db()
    assert push_row.status == DeliveryStatus.SENT
    assert whatsapp_row.status == DeliveryStatus.PROCESSING
    assert len(push_backends.outbox) == 1

    # The sweep/resume path re-runs only the WhatsApp row: no second push.
    execute_deliveries(delivery_ids=[str(push_row.pk), str(whatsapp_row.pk)])
    assert len(push_backends.outbox) == 1


def test_sms_rows_the_provider_accepted_before_a_rejection_are_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMSMisr posts one number at a time and stops at the first bad code; the
    numbers before it are with the provider. Marking the whole group FAILED
    would re-bill them on the next `--include-failed` sweep."""
    context = {"amount": "10.00", "currency": "SAR"}
    rows = []
    for phone in ("+201001234567", "+201001234568", "+201001234569"):
        user = UserFactory.create(phone=phone, language="en")
        notification = NotificationFactory.create(
            recipient=user, kind=NotificationKind.PAYMENT_PAID, context=context
        )
        rows.append(
            NotificationDeliveryFactory.create(
                notification=notification, channel=Channel.SMS
            )
        )

    def _partial(*, to: list[str], body: str) -> None:
        raise SmsProviderError(provider="smsmisr", detail="code='1905'", sent=to[:1])

    monkeypatch.setattr("apps.notifications.tasks.delivery.sms_send_many", _partial)

    execute_deliveries(delivery_ids=[str(row.pk) for row in rows])

    for row in rows:
        row.refresh_from_db()
    assert rows[0].status == DeliveryStatus.SENT
    assert rows[0].sent_at is not None
    assert [row.status for row in rows[1:]] == [DeliveryStatus.FAILED] * 2
    assert rows[1].detail == "smsmisr: code='1905'"


def test_a_channel_with_no_deliverer_fails_instead_of_staying_claimed() -> None:
    """A claimed-but-unroutable row would be reset and re-claimed by every
    sweep while its broadcast never completed."""
    delivery = NotificationDeliveryFactory.create(
        notification=NotificationFactory.create(), channel="email"
    )

    execute_deliveries(delivery_ids=[str(delivery.pk)])

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert "email" in delivery.detail
