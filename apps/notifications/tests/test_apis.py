"""Inbox + device endpoints."""

import pytest
from django.test import Client

from apps.notifications.constants import NotificationKind
from apps.notifications.models import Device
from apps.notifications.models import Notification
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LIST = "/api/v1/notifications"
DEVICES = "/api/v1/notifications/devices"


def test_endpoints_require_auth(client: Client) -> None:
    assert client.get(f"{LIST}/").status_code == 401
    assert client.post(DEVICES, {}, content_type="application/json").status_code == 401


def test_device_register_and_unregister_roundtrip(client: Client) -> None:
    user = UserFactory.create()
    client.force_login(user)

    response = client.post(
        DEVICES,
        {"registration_id": "tok-abc", "platform": "android"},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["platform"] == "android"
    assert Device.objects.filter(user=user, registration_id="tok-abc").exists()

    assert client.delete(f"{DEVICES}/tok-abc").status_code == 204
    assert not Device.objects.filter(user=user).exists()


def test_inbox_lists_only_own_rows_rendered(client: Client) -> None:
    user = UserFactory.create()
    NotificationFactory.create_batch(2, recipient=user, context={"name": "Omar"})
    NotificationFactory.create()  # someone else's
    client.force_login(user)

    body = client.get(f"{LIST}/").json()

    assert len(body["items"]) == 2
    item = body["items"][0]
    assert item["title"]  # rendered from the kind's config row, not stored
    assert "Omar" in item["body"]
    assert item["read_at"] is None
    assert "next_cursor" in body


def test_inbox_renders_edited_copy_per_request_locale(client: Client) -> None:
    """Render-at-read against the LIVE config row: an admin edit shows up in
    old inbox rows immediately, in the requester's language."""
    NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).update(
        body_ar="أهلاً {name}", body_en="Hey {name}"
    )
    user = UserFactory.create()
    NotificationFactory.create(recipient=user, context={"name": "Omar"})
    client.force_login(user)

    english = client.get(f"{LIST}/", headers={"Accept-Language": "en"}).json()
    arabic = client.get(f"{LIST}/", headers={"Accept-Language": "ar"}).json()

    assert english["items"][0]["body"] == "Hey Omar"
    assert arabic["items"][0]["body"] == "أهلاً Omar"


def test_unread_count_and_read_flow(client: Client) -> None:
    user = UserFactory.create()
    first, second = NotificationFactory.create_batch(2, recipient=user)
    client.force_login(user)

    assert client.get(f"{LIST}/unread-count").json() == {"unread": 2}

    read_response = client.post(f"{LIST}/{first.pk}/read")
    assert read_response.status_code == 200
    assert read_response.json()["read_at"] is not None

    assert client.get(f"{LIST}/unread-count").json() == {"unread": 1}
    assert client.post(f"{LIST}/read-all").json() == {"updated": 1}
    assert client.get(f"{LIST}/unread-count").json() == {"unread": 0}
    second.refresh_from_db()
    assert second.read_at is not None  # read via read-all


def test_reading_someone_elses_notification_is_404(client: Client) -> None:
    other = NotificationFactory.create()
    user = UserFactory.create()
    client.force_login(user)

    response = client.post(f"{LIST}/{other.pk}/read")

    assert response.status_code == 404
    body = response.json()
    assert body["extra"]["code"] == "notification_not_found"


def test_delete_one_cascades_deliveries_and_is_scoped(client: Client) -> None:
    mine = NotificationFactory.create()
    delivery = NotificationDeliveryFactory.create(notification=mine)
    other = NotificationFactory.create()
    client.force_login(mine.recipient)

    assert client.delete(f"{LIST}/{other.pk}").status_code == 404  # not mine
    assert client.delete(f"{LIST}/{mine.pk}").status_code == 204
    assert not Notification.objects.filter(pk=mine.pk).exists()
    assert not NotificationDelivery.objects.filter(pk=delivery.pk).exists()


def test_delete_all_clears_only_my_inbox(client: Client) -> None:
    user = UserFactory.create()
    NotificationFactory.create_batch(2, recipient=user)
    other = NotificationFactory.create()
    client.force_login(user)

    assert client.post(f"{LIST}/delete-all").json() == {"deleted": 2}
    assert not Notification.objects.filter(recipient=user).exists()
    assert Notification.objects.filter(pk=other.pk).exists()
    assert client.post(f"{LIST}/delete-all").json() == {"deleted": 0}
