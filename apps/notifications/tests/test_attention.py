"""Nothing runs on a timer: stuck deliveries must be VISIBLE (sidebar badge,
"Needs attention" filter) and recoverable by hand ("Re-queue stuck")."""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.notifications import selectors
from apps.notifications.admin.badges import deliveries_needing_attention
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.models import NotificationDelivery
from apps.notifications.tests.factories import BroadcastFactory
from apps.notifications.tests.factories import DeviceFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

CONFIRM = {"_form_submitted": "1"}


def _stuck(**kwargs: Any) -> NotificationDelivery:
    delivery = NotificationDeliveryFactory.create(channel=Channel.PUSH, **kwargs)
    NotificationDelivery.objects.filter(pk=delivery.pk).update(
        status=DeliveryStatus.PROCESSING,
        updated_at=timezone.now() - timedelta(hours=1),
    )
    delivery.refresh_from_db()
    return delivery


def test_attention_lists_stale_processing_and_failed_transactional() -> None:
    stuck = _stuck()
    in_flight = NotificationDeliveryFactory.create(status=DeliveryStatus.PROCESSING)
    failed = NotificationDeliveryFactory.create(status=DeliveryStatus.FAILED)
    broadcast = BroadcastFactory.create(status=BroadcastStatus.COMPLETED)
    failed_in_broadcast = NotificationDeliveryFactory.create(
        notification=NotificationFactory.create(broadcast=broadcast),
        status=DeliveryStatus.FAILED,
    )

    flagged = set(selectors.deliveries_needing_attention())

    assert {stuck, failed} <= flagged
    assert not {in_flight, failed_in_broadcast} & flagged  # broadcasts: Resume


def test_badge_counts_for_a_viewer_and_hides_when_clean(rf: Any) -> None:
    request = rf.get("/admin/")
    request.user = UserFactory.create(is_staff=True, is_superuser=True)
    baseline = selectors.deliveries_needing_attention().count()
    _stuck()

    assert deliveries_needing_attention(request) == str(baseline + 1)
    request.user = UserFactory.create(is_staff=True)
    assert deliveries_needing_attention(request) == ""


def test_requeue_action_resends_what_a_dead_worker_left(
    client: Client, run_enqueued_tasks: Any
) -> None:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    delivery = _stuck()
    DeviceFactory.create(user=delivery.notification.recipient)
    url = reverse("admin:notifications_notificationdelivery_requeue_stuck")

    assert client.get(url).status_code == 200  # the dialog, no side effect
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.PROCESSING
    with run_enqueued_tasks():
        response = client.post(url, CONFIRM)

    assert response.status_code == 302
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT


def test_requeue_action_needs_the_change_permission(client: Client) -> None:
    client.force_login(UserFactory.create(is_staff=True))

    response = client.post(
        reverse("admin:notifications_notificationdelivery_requeue_stuck"), CONFIRM
    )

    assert response.status_code in (302, 403)
