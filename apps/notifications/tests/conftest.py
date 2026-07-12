"""Notifications test fixtures."""

import pytest

from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.sms import backends as sms_backends


@pytest.fixture(autouse=True)
def _clear_client_outboxes() -> None:
    """Locmem SMS/push outboxes start empty in every test."""
    sms_backends.outbox.clear()
    push_backends.outbox.clear()
