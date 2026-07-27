"""Broadcast lifecycle: dispatch paging, eligibility, resume, guards."""

import io
from typing import Any

import pytest
from django.core.management import call_command

from apps.notifications import services
from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.sms import backends as sms_backends
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import BroadcastStateError
from apps.notifications.exceptions import BroadcastTooLargeForInlineError
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.tasks import broadcast as broadcast_tasks
from apps.notifications.tests.factories import BroadcastFactory
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationChannelOverrideFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise multi-page dispatch without thousands of rows."""
    monkeypatch.setattr(broadcast_tasks, "DISPATCH_PAGE", 2)
    monkeypatch.setattr(broadcast_tasks, "DELIVERY_BATCH", 2)


@pytest.fixture(autouse=True)
def _exclusive_audience(db: Any) -> None:
    """Deactivate every pre-existing user (committed leftovers under
    --reuse-db) so the audience is exactly what the test creates - including
    each BroadcastFactory's created_by. The UPDATE rolls back with the test
    transaction."""
    from apps.users.models import User

    User.objects.update(is_active=False)


def _dispatch(broadcast: Any, django_capture_on_commit_callbacks: Any) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        services.broadcast_dispatch(broadcast=broadcast)


def test_dispatch_pages_the_audience_and_delivers(
    django_capture_on_commit_callbacks: Any,
) -> None:
    users = [UserFactory.create() for _ in range(5)]
    for user in users:
        DeviceFactory.create(user=user)
    broadcast = BroadcastFactory.create()  # created_by joins the audience too

    _dispatch(broadcast, django_capture_on_commit_callbacks)

    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.COMPLETED
    assert broadcast.total_recipients == 6  # 5 + the (device-less) author
    assert broadcast.total_deliveries == 5
    assert broadcast.sent_count == 5
    assert Notification.objects.filter(broadcast=broadcast).count() == 6
    assert len(push_backends.outbox) == 5


def test_dispatch_writes_inbox_rows_even_for_ineligible_users(
    django_capture_on_commit_callbacks: Any,
) -> None:
    """No device -> no PUSH delivery row, but the inbox row always exists."""
    UserFactory.create()  # no device
    broadcast = BroadcastFactory.create()  # author is device-less too

    _dispatch(broadcast, django_capture_on_commit_callbacks)

    broadcast.refresh_from_db()
    assert broadcast.total_recipients == 2
    assert broadcast.total_deliveries == 0
    assert Notification.objects.filter(broadcast=broadcast).count() == 2
    assert broadcast.status == BroadcastStatus.COMPLETED  # zero-delivery finish


def test_dispatch_respects_channel_capabilities(
    django_capture_on_commit_callbacks: Any,
) -> None:
    NotificationChannelOverrideFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, channel=Channel.SMS, enabled=True
    )
    with_phone = UserFactory.create(phone="+966501234567")
    DeviceFactory.create(user=with_phone)
    phoneless = UserFactory.create()
    DeviceFactory.create(user=phoneless)
    broadcast = BroadcastFactory.create()

    _dispatch(broadcast, django_capture_on_commit_callbacks)

    by_user = {
        (d.notification.recipient_id, d.channel)
        for d in NotificationDelivery.objects.filter(broadcast=broadcast)
    }
    assert (with_phone.pk, Channel.SMS) in by_user
    assert (with_phone.pk, Channel.PUSH) in by_user
    assert (phoneless.pk, Channel.PUSH) in by_user
    assert (phoneless.pk, Channel.SMS) not in by_user  # fan-out-time decision
    assert [entry.to for entry in sms_backends.outbox] == ["+966501234567"]


def test_dispatch_filters_audience_by_language(
    django_capture_on_commit_callbacks: Any,
) -> None:
    arabic = UserFactory.create(language="ar")
    english = UserFactory.create(language="en")
    broadcast = BroadcastFactory.create(language="ar")

    _dispatch(broadcast, django_capture_on_commit_callbacks)

    recipients = set(
        Notification.objects.filter(broadcast=broadcast).values_list(
            "recipient_id", flat=True
        )
    )
    assert arabic.pk in recipients
    assert english.pk not in recipients


def test_dispatch_twice_raises_state_error(
    django_capture_on_commit_callbacks: Any,
) -> None:
    broadcast = BroadcastFactory.create()
    _dispatch(broadcast, django_capture_on_commit_callbacks)

    with pytest.raises(BroadcastStateError):
        services.broadcast_dispatch(broadcast=broadcast)


def test_inline_backend_refuses_large_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services.broadcasts, "INLINE_AUDIENCE_LIMIT", 1)
    UserFactory.create()
    UserFactory.create()
    broadcast = BroadcastFactory.create()

    with pytest.raises(BroadcastTooLargeForInlineError):
        services.broadcast_dispatch(broadcast=broadcast)


def test_broadcast_validates_context() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        services.notification_broadcast(
            kind=NotificationKind.ANNOUNCEMENT,
            context={"message": "x", "extra": "y"},
            actor=UserFactory.create(),
        )


# --- resume -------------------------------------------------------------------


def test_resume_resets_stale_processing_and_completes(
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create()
    DeviceFactory.create(user=user)
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )
    # Simulate a worker that died mid-batch, 20 minutes ago.
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.PROCESSING
    )

    with django_capture_on_commit_callbacks(execute=True):
        summary = services.broadcast_resume(broadcast=broadcast, stale_minutes=0)

    assert summary["stale_reset"] == 1
    assert summary["re_enqueued"] == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.COMPLETED


def test_resume_of_draft_raises() -> None:
    broadcast = BroadcastFactory.create()

    with pytest.raises(BroadcastStateError):
        services.broadcast_resume(broadcast=broadcast)


def test_resume_can_retry_failed_rows(
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create()
    DeviceFactory.create(user=user)
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.FAILED, detail="quota"
    )

    with django_capture_on_commit_callbacks(execute=True):
        summary = services.broadcast_resume(broadcast=broadcast, include_failed=True)

    assert summary["failed_reset"] == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT


# --- sweep command ------------------------------------------------------------


def test_sweep_command_recovers_transactional_orphans(
    django_capture_on_commit_callbacks: Any,
) -> None:
    delivery = NotificationDeliveryFactory.create(channel=Channel.PUSH)
    DeviceFactory.create(user=delivery.notification.recipient)
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.PROCESSING
    )

    out = io.StringIO()
    with django_capture_on_commit_callbacks(execute=True):
        call_command("sweep_deliveries", "--stale-minutes", "0", stdout=out)

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    assert "stale_reset: 1" in out.getvalue()


def test_sweep_command_scoped_to_a_broadcast(
    django_capture_on_commit_callbacks: Any,
) -> None:
    user = UserFactory.create()
    DeviceFactory.create(user=user)
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )
    NotificationDeliveryFactory.create(notification=notification, channel=Channel.PUSH)

    out = io.StringIO()
    with django_capture_on_commit_callbacks(execute=True):
        call_command("sweep_deliveries", "--broadcast", str(broadcast.pk), stdout=out)

    assert "re_enqueued: 1" in out.getvalue()
    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.COMPLETED
