"""Notifications test fixtures."""

import pytest

from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.sms import backends as sms_backends
from apps.notifications.clients.whatsapp import backends as whatsapp_backends


@pytest.fixture(autouse=True)
def _clear_client_outboxes() -> None:
    """Locmem SMS/push/WhatsApp outboxes start empty in every test."""
    sms_backends.outbox.clear()
    push_backends.outbox.clear()
    whatsapp_backends.outbox.clear()


@pytest.fixture(autouse=True)
def _deterministic_channel_policy(request: pytest.FixtureRequest) -> None:
    """Channel resolution starts from catalog defaults in every DB test.

    --reuse-db keeps rows committed by earlier runs; a leftover override row
    would silently flip channels for every test that sends. Tests that WANT
    overrides create them after this fixture ran. Client-only tests (no
    django_db marker) skip the query entirely.
    """
    if request.node.get_closest_marker("django_db") is None:
        return
    request.getfixturevalue("db")
    from apps.notifications.models import NotificationChannelOverride

    NotificationChannelOverride.objects.all().delete()
