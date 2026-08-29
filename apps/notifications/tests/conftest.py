"""Notifications test fixtures."""

import pytest

from apps.notifications.clients import push as push_client
from apps.notifications.clients import sms as sms_client
from apps.notifications.clients import whatsapp as whatsapp_client
from apps.notifications.tests import locmem


@pytest.fixture(autouse=True)
def outboxes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test sends SMS/push/WhatsApp into empty in-memory outboxes.

    The three client ``_backend`` seams are the ONLY switch - there is no
    settings-string backend. A test that wants a different transport
    monkeypatches the same seam (e.g. MetaWhatsAppBackend for the
    not-configured path).
    """
    monkeypatch.setattr(sms_client, "_backend", locmem.LocmemSmsBackend)
    monkeypatch.setattr(push_client, "_backend", locmem.LocmemPushBackend)
    monkeypatch.setattr(whatsapp_client, "_backend", locmem.LocmemWhatsAppBackend)
    locmem.sms_outbox.clear()
    locmem.push_outbox.clear()
    locmem.whatsapp_outbox.clear()


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
