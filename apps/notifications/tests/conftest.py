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
def _catalog_seed_config(request: pytest.FixtureRequest) -> None:
    """Every DB test starts from catalog-seeded config rows.

    --reuse-db keeps edits committed by earlier runs, and rendering has no
    code fallback - so each test restores the exact state migration 0005
    wrote (which also self-heals a reused DB that predates the rows). Tests
    that WANT different channels or copy edit rows after this ran.
    Client-only tests (no django_db marker) skip the queries entirely.
    """
    if request.node.get_closest_marker("django_db") is None:
        return
    request.getfixturevalue("db")
    from apps.notifications.constants import NotificationKind
    from apps.notifications.models import NotificationKindConfig
    from apps.notifications.tests.factories import kind_config_seed

    for kind in NotificationKind:
        NotificationKindConfig.objects.update_or_create(
            kind=kind, defaults=kind_config_seed(kind)
        )
