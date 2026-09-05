"""Broadcast lifecycle: dispatch paging, eligibility, resume, guards."""

import io
import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.utils import timezone
from django.utils.translation import gettext

from apps.notifications import selectors
from apps.notifications import services
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import BroadcastAudienceError
from apps.notifications.exceptions import BroadcastStateError
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig
from apps.notifications.services import dispatch as dispatch_service
from apps.notifications.tests.factories import BroadcastFactory
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.notifications.tests.locmem import push_outbox
from apps.notifications.tests.locmem import sms_outbox
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _small_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise multi-page dispatch without thousands of rows."""
    monkeypatch.setattr(dispatch_service, "DISPATCH_PAGE", 2)
    monkeypatch.setattr(dispatch_service, "DELIVERY_BATCH", 2)


@pytest.fixture(autouse=True)
def _exclusive_audience(db: Any) -> None:
    """Deactivate every pre-existing user (the session fixtures commit
    users outside the test transaction) so the audience is exactly what the
    test creates - including each BroadcastFactory's created_by. The UPDATE
    rolls back with the test transaction."""
    from apps.users.models import User

    User.objects.update(is_active=False)


def _dispatch(broadcast: Any, run_enqueued_tasks: Any) -> None:
    with run_enqueued_tasks():
        services.broadcast_dispatch(broadcast=broadcast)


def test_dispatch_pages_the_audience_and_delivers(
    run_enqueued_tasks: Any,
) -> None:
    users = [UserFactory.create() for _ in range(5)]
    for user in users:
        DeviceFactory.create(user=user)
    broadcast = BroadcastFactory.create()  # created_by joins the audience too

    _dispatch(broadcast, run_enqueued_tasks)

    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.COMPLETED
    assert broadcast.total_recipients == 6  # 5 + the (device-less) author
    assert broadcast.total_deliveries == 5
    assert broadcast.sent_count == 5
    assert Notification.objects.filter(broadcast=broadcast).count() == 6
    assert len(push_outbox) == 5


def test_dispatch_writes_inbox_rows_even_for_ineligible_users(
    run_enqueued_tasks: Any,
) -> None:
    """No device -> no PUSH delivery row, but the inbox row always exists."""
    UserFactory.create()  # no device
    broadcast = BroadcastFactory.create()  # author is device-less too

    _dispatch(broadcast, run_enqueued_tasks)

    broadcast.refresh_from_db()
    assert broadcast.total_recipients == 2
    assert broadcast.total_deliveries == 0
    assert Notification.objects.filter(broadcast=broadcast).count() == 2
    assert broadcast.status == BroadcastStatus.COMPLETED  # zero-delivery finish


def test_dispatch_respects_channel_capabilities(
    run_enqueued_tasks: Any,
) -> None:
    with_phone = UserFactory.create(phone="+966501234567")
    DeviceFactory.create(user=with_phone)
    phoneless = UserFactory.create()
    DeviceFactory.create(user=phoneless)
    broadcast = BroadcastFactory.create(channels=[Channel.PUSH, Channel.SMS])

    _dispatch(broadcast, run_enqueued_tasks)

    by_user = {
        (d.notification.recipient_id, d.channel)
        for d in NotificationDelivery.objects.filter(broadcast=broadcast)
    }
    assert (with_phone.pk, Channel.SMS) in by_user
    assert (with_phone.pk, Channel.PUSH) in by_user
    assert (phoneless.pk, Channel.PUSH) in by_user
    assert (phoneless.pk, Channel.SMS) not in by_user  # fan-out-time decision
    assert [entry.to for entry in sms_outbox] == ["+966501234567"]


def test_dispatch_filters_audience_by_language(
    run_enqueued_tasks: Any,
) -> None:
    arabic = UserFactory.create(language="ar")
    english = UserFactory.create(language="en")
    broadcast = BroadcastFactory.create(language="ar")

    _dispatch(broadcast, run_enqueued_tasks)

    recipients = set(
        Notification.objects.filter(broadcast=broadcast).values_list(
            "recipient_id", flat=True
        )
    )
    assert arabic.pk in recipients
    assert english.pk not in recipients


def test_dispatch_twice_raises_state_error(
    run_enqueued_tasks: Any,
) -> None:
    broadcast = BroadcastFactory.create()
    _dispatch(broadcast, run_enqueued_tasks)

    with pytest.raises(BroadcastStateError):
        services.broadcast_dispatch(broadcast=broadcast)


def _author(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "kind": NotificationKind.ANNOUNCEMENT,
        "context": {"title": "t", "message": "m"},
        "language": "",
        "require_device": False,
        "joined_after": None,
        "joined_before": None,
        "channels": [Channel.PUSH],
        "recipient_ids": [],
        "actor": UserFactory.create(),
    }
    return services.notification_broadcast(**{**kwargs, **overrides})


def test_broadcast_validates_context() -> None:
    with pytest.raises(ValueError, match="unexpected"):
        _author(context={"message": "x", "extra": "y"})


class TestChannels:
    """Every broadcast carries its own non-empty, supported channel list."""

    def test_a_selection_is_stored_sorted(self) -> None:
        assert _author(channels=[Channel.SMS, Channel.PUSH]).channels == [
            "push",
            "sms",
        ]

    def test_an_empty_override_is_rejected(self) -> None:
        with pytest.raises(BroadcastAudienceError) as excinfo:
            _author(channels=[])
        assert excinfo.value.message == gettext("Pick at least one channel.")

    def test_an_unsupported_channel_is_rejected(self) -> None:
        with pytest.raises(BroadcastAudienceError, match="carrier_pigeon"):
            _author(channels=["carrier_pigeon"])


# --- resume -------------------------------------------------------------------


def test_resume_resets_stale_processing_and_completes(
    run_enqueued_tasks: Any,
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
    # Simulate a worker that died mid-batch, an hour ago.
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.PROCESSING,
        updated_at=timezone.now() - timedelta(hours=1),
    )

    with run_enqueued_tasks():
        summary = services.deliveries_resume(broadcast=broadcast, include_failed=False)

    assert summary["stale_reset"] == 1
    assert summary["re_enqueued"] == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.COMPLETED


def test_resume_leaves_a_fresh_processing_row_to_its_worker() -> None:
    """Below STALE_PROCESSING_MINUTES the row is still someone's batch."""
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    notification = NotificationFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.PROCESSING
    )

    summary = services.deliveries_resume(broadcast=broadcast, include_failed=False)

    assert summary["stale_reset"] == 0
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PROCESSING


def test_resume_of_draft_raises() -> None:
    broadcast = BroadcastFactory.create()

    with pytest.raises(BroadcastStateError):
        services.deliveries_resume(broadcast=broadcast, include_failed=False)


def test_resume_reenqueues_a_dead_dispatcher(
    run_enqueued_tasks: Any,
) -> None:
    """DISPATCHING with no worker alive: the cursor committed with its rows,
    so a fresh dispatcher run finishes the fan-out."""
    UserFactory.create()
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHING)

    with run_enqueued_tasks():
        summary = services.deliveries_resume(broadcast=broadcast, include_failed=False)

    assert summary["dispatcher_reenqueued"] == 1
    broadcast.refresh_from_db()
    assert broadcast.status == BroadcastStatus.COMPLETED


def test_resume_can_retry_failed_rows(
    run_enqueued_tasks: Any,
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

    with run_enqueued_tasks():
        summary = services.deliveries_resume(broadcast=broadcast, include_failed=True)

    assert summary["failed_reset"] == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT


# --- sweep command ------------------------------------------------------------


def test_sweep_command_recovers_transactional_orphans(
    run_enqueued_tasks: Any,
) -> None:
    delivery = NotificationDeliveryFactory.create(channel=Channel.PUSH)
    DeviceFactory.create(user=delivery.notification.recipient)
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.PROCESSING,
        updated_at=timezone.now() - timedelta(hours=1),
    )

    out = io.StringIO()
    with run_enqueued_tasks():
        call_command("sweep_deliveries", stdout=out)

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT
    assert "stale_reset: 1" in out.getvalue()


def test_sweep_command_leaves_broadcast_rows_alone(
    run_enqueued_tasks: Any,
) -> None:
    """Broadcasts resume from their admin page; the scheduled sweep only
    touches transactional orphans."""
    user = UserFactory.create()
    DeviceFactory.create(user=user)
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    notification = NotificationFactory.create(
        recipient=user, kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )
    delivery = NotificationDeliveryFactory.create(
        notification=notification, channel=Channel.PUSH
    )

    transactional_pending = NotificationDelivery.objects.filter(
        broadcast__isnull=True, status=DeliveryStatus.PENDING
    ).count()

    out = io.StringIO()
    with run_enqueued_tasks():
        call_command("sweep_deliveries", "--include-failed", stdout=out)

    # Only the transactional rows (whatever other fixtures left behind) move.
    assert f"re_enqueued: {transactional_pending}" in out.getvalue()
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PENDING


class TestAudienceFilters:
    """`broadcast_audience` is the single audience definition - the dispatcher
    pages it and the composer's estimate counts it, so a filter that
    duplicates rows would double-send."""

    def test_require_device_excludes_users_without_one(self) -> None:
        with_device = UserFactory.create()
        DeviceFactory.create(user=with_device)
        UserFactory.create()  # no device
        broadcast = BroadcastFactory.create(require_device=True)

        audience = selectors.broadcast_audience(broadcast=broadcast)

        assert list(audience.values_list("pk", flat=True)) == [with_device.pk]

    def test_require_device_counts_a_multi_device_user_once(self) -> None:
        user = UserFactory.create()
        DeviceFactory.create(user=user)
        DeviceFactory.create(user=user)
        broadcast = BroadcastFactory.create(require_device=True)

        audience = selectors.broadcast_audience(broadcast=broadcast)

        # A `devices__isnull=False` join would return this user twice, and the
        # dispatcher's pk-cursor paging would send to them twice.
        assert list(audience.values_list("pk", flat=True)) == [user.pk]

    def test_joined_between_bounds_are_inclusive(self) -> None:
        from apps.users.models import User

        early, inside, late = UserFactory.create_batch(3)
        dated = ((early, "2026-01-01"), (inside, "2026-03-15"), (late, "2026-06-30"))
        for user, day in dated:
            User.objects.filter(pk=user.pk).update(created_at=f"{day}T12:00:00Z")
        broadcast = BroadcastFactory.create(
            joined_after="2026-01-01", joined_before="2026-06-30"
        )

        audience = selectors.broadcast_audience(broadcast=broadcast)

        assert set(audience.values_list("pk", flat=True)) == {
            early.pk,
            inside.pk,
            late.pk,
        }

    def test_joined_after_excludes_earlier_signups(self) -> None:
        from apps.users.models import User

        old, recent = UserFactory.create_batch(2)
        User.objects.filter(pk=old.pk).update(created_at="2025-01-01T00:00:00Z")
        User.objects.filter(pk=recent.pk).update(created_at="2026-05-01T00:00:00Z")
        # Pin the author to a user this filter excludes: BroadcastFactory's
        # SubFactory mints a fresh, today-dated user *after* _exclusive_audience
        # ran, and that author would otherwise land in the audience.
        broadcast = BroadcastFactory.create(joined_after="2026-01-01", created_by=old)

        audience = selectors.broadcast_audience(broadcast=broadcast)

        assert list(audience.values_list("pk", flat=True)) == [recent.pk]


class TestSpecificRecipients:
    """A hand-picked audience: exactly those users, filters ignored."""

    def test_recipients_are_stored_deduplicated_and_sorted(self) -> None:
        a, b = UserFactory.create(), UserFactory.create()

        broadcast = _author(recipient_ids=[str(b.pk), str(a.pk), str(b.pk)])

        assert broadcast.recipient_ids == sorted([str(a.pk), str(b.pk)])

    def test_an_unknown_or_inactive_user_is_rejected(self) -> None:
        inactive = UserFactory.create(is_active=False)

        expected = gettext(
            "%(count)d selected user(s) no longer exist or are inactive."
        ) % {"count": 1}
        with pytest.raises(BroadcastAudienceError) as excinfo:
            _author(recipient_ids=[str(inactive.pk)])
        assert excinfo.value.message == expected
        with pytest.raises(BroadcastAudienceError):
            _author(recipient_ids=[str(uuid.uuid4())])

    def test_audience_is_exactly_the_picked_users(self) -> None:
        picked = UserFactory.create(language="ar")
        UserFactory.create(language="ar")  # same language, not picked
        broadcast = BroadcastFactory.create(
            recipient_ids=[str(picked.pk)],
            language="en",  # filter ignored
        )

        audience = selectors.broadcast_audience(broadcast=broadcast)

        assert list(audience.values_list("pk", flat=True)) == [picked.pk]

    def test_require_device_still_applies_to_picked_users(self) -> None:
        with_device, without = UserFactory.create(), UserFactory.create()
        DeviceFactory.create(user=with_device)
        broadcast = BroadcastFactory.create(
            recipient_ids=[str(with_device.pk), str(without.pk)], require_device=True
        )

        audience = selectors.broadcast_audience(broadcast=broadcast)

        assert list(audience.values_list("pk", flat=True)) == [with_device.pk]

    def test_search_matches_name_email_or_phone_of_active_users(self) -> None:
        omar = UserFactory.create(name="Omar Gawdat", email="omar@x.test")
        UserFactory.create(name="Sara", email="sara@x.test", is_active=False)
        by_phone = UserFactory.create(name="Zed", phone="+966501234567")

        assert list(selectors.broadcast_user_search(query="omar")) == [omar]
        assert list(selectors.broadcast_user_search(query="sara")) == []
        assert list(selectors.broadcast_user_search(query="50123")) == [by_phone]
        assert list(selectors.broadcast_user_search(query="  ")) == []


class TestPerBroadcastChannels:
    def test_a_broadcast_sends_on_exactly_its_own_channels(self) -> None:
        """The kind's config row is not consulted - the pick is the policy."""
        NotificationKindConfig.objects.filter(
            kind=NotificationKind.ANNOUNCEMENT
        ).update(channels=[Channel.PUSH])
        broadcast = BroadcastFactory.create(channels=[Channel.SMS])

        channels = selectors.effective_channels(
            kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
        )

        assert channels == frozenset({Channel.SMS})

    def test_a_broadcast_with_no_channels_is_invalid(self) -> None:
        with pytest.raises(ValidationError, match="channels"):
            BroadcastFactory.build(channels=[]).full_clean()

    def test_a_channel_the_kind_no_longer_supports_is_dropped(self) -> None:
        """Defence in depth: a row written before a channel was withdrawn from
        the catalog must not resurrect it."""
        broadcast = BroadcastFactory.create(channels=[Channel.PUSH, "carrier_pigeon"])

        with pytest.raises(ValueError, match="carrier_pigeon"):
            selectors.effective_channels(
                kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
            )


# --- counters count rows in a state, never attempts (review 2026-09-05, #8) ----


def test_retry_replaces_a_failed_outcome_instead_of_counting_it_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.notifications.services import execution

    broadcast = BroadcastFactory.create(
        status=BroadcastStatus.DISPATCHED, total_recipients=1, total_deliveries=1
    )
    notification = NotificationFactory.create(broadcast=broadcast)
    delivery = NotificationDeliveryFactory.create(
        notification=notification,
        broadcast=broadcast,
        channel=Channel.PUSH,
        status=DeliveryStatus.PENDING,
    )
    outcomes = iter([DeliveryStatus.FAILED, DeliveryStatus.SENT])

    def deliver(rows: Any, *, configs: Any) -> None:
        outcome = next(outcomes)
        for row in rows:
            row.status = outcome
            row.detail = "provider said no" if outcome == DeliveryStatus.FAILED else ""
            row.sent_at = timezone.now() if outcome == DeliveryStatus.SENT else None

    monkeypatch.setitem(execution._DELIVERERS, Channel.PUSH, deliver)

    execution.execute_deliveries(delivery_ids=[str(delivery.pk)])
    broadcast.refresh_from_db()
    assert (broadcast.sent_count, broadcast.failed_count) == (0, 1)

    services.deliveries_resume(broadcast=broadcast, include_failed=True)
    execution.execute_deliveries(delivery_ids=[str(delivery.pk)])

    broadcast.refresh_from_db()
    assert (broadcast.sent_count, broadcast.failed_count, broadcast.skipped_count) == (
        1,
        0,
        0,
    )
    assert (
        broadcast.sent_count + broadcast.failed_count + broadcast.skipped_count
        == broadcast.total_deliveries
    )
    assert broadcast.status == BroadcastStatus.COMPLETED


def test_provider_failing_a_sent_row_moves_the_counters_with_it() -> None:
    broadcast = BroadcastFactory.create(
        status=BroadcastStatus.COMPLETED, total_deliveries=1, sent_count=1
    )
    notification = NotificationFactory.create(broadcast=broadcast)
    NotificationDeliveryFactory.create(
        notification=notification,
        broadcast=broadcast,
        channel=Channel.WHATSAPP,
        status=DeliveryStatus.SENT,
        provider="whatsapp",
        provider_message_id="wamid.1",
    )

    assert services.delivery_update_status(
        provider="whatsapp",
        provider_message_id="wamid.1",
        status=DeliveryStatus.FAILED,
        detail="bounced",
    )

    broadcast.refresh_from_db()
    assert (broadcast.sent_count, broadcast.failed_count) == (0, 1)


def test_resume_is_bounded_and_reports_the_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from apps.notifications.services import deliveries

    monkeypatch.setattr(deliveries, "RESUME_LIMIT", 2)
    broadcast = BroadcastFactory.create(status=BroadcastStatus.DISPATCHED)
    for _ in range(3):
        NotificationDeliveryFactory.create(
            notification=NotificationFactory.create(broadcast=broadcast),
            broadcast=broadcast,
            channel=Channel.PUSH,
            status=DeliveryStatus.PENDING,
        )

    summary = services.deliveries_resume(broadcast=broadcast, include_failed=False)

    assert summary["re_enqueued"] == 2
    assert summary["remaining"] == 1
