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
    """Every DB test starts from every kind's row in its catalog starting
    state (as if an operator had saved each card once).

    --reuse-db keeps edits committed by earlier runs, so each test restores
    the exact state. Tests that WANT different channels or copy (or no row)
    edit after this ran. Client-only tests (no django_db marker) skip the
    queries entirely.
    """
    if request.node.get_closest_marker("django_db") is None:
        return
    request.getfixturevalue("db")
    from apps.notifications.tests.factories import seed_kind_configs

    seed_kind_configs()
