"""FCM backend: chunking, invalid-token reporting, configuration guard."""

from collections.abc import Mapping
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from firebase_admin import messaging

from apps.notifications.clients.push import backends as push_backends
from apps.notifications.clients.push import fcm as fcm_module
from apps.notifications.clients.push import push_send
from apps.notifications.clients.push.base import PushNotConfiguredError
from apps.notifications.clients.push.base import PushReport
from apps.notifications.clients.push.fcm import FcmPushBackend


@pytest.fixture
def fake_firebase(monkeypatch: pytest.MonkeyPatch) -> list[list[messaging.Message]]:
    """Bypass credentials and capture every send_each batch."""
    batches: list[list[messaging.Message]] = []

    def fake_send_each(
        messages: list[messaging.Message], app: Any = None
    ) -> SimpleNamespace:
        batches.append(messages)
        responses = []
        for message in messages:
            if message.token.startswith("dead-"):
                responses.append(
                    SimpleNamespace(
                        success=False,
                        exception=messaging.UnregisteredError("token gone"),
                    )
                )
            else:
                responses.append(SimpleNamespace(success=True, exception=None))
        return SimpleNamespace(responses=responses)

    monkeypatch.setattr(fcm_module, "_firebase_app", lambda: None)
    monkeypatch.setattr(messaging, "send_each", fake_send_each)
    return batches


def test_fcm_chunks_at_500(fake_firebase: list[list[messaging.Message]]) -> None:
    tokens = [f"token-{i}" for i in range(1001)]

    report = FcmPushBackend().send(tokens=tokens, title="t", body="b", data={})

    assert [len(batch) for batch in fake_firebase] == [500, 500, 1]
    assert report.sent == 1001
    assert report.failed == 0


def test_fcm_reports_unregistered_tokens(
    fake_firebase: list[list[messaging.Message]],
) -> None:
    report = FcmPushBackend().send(
        tokens=["live-1", "dead-1", "live-2"],
        title="t",
        body="b",
        data={"k": "v"},
    )

    assert report.sent == 2
    assert report.failed == 1
    assert report.invalid_tokens == ("dead-1",)


def test_fcm_without_creds_is_loud() -> None:
    # FIREBASE_CREDENTIALS_B64 is None in test settings; exceptions are not
    # cached by functools.cache, so this stays deterministic.
    with pytest.raises(PushNotConfiguredError):
        FcmPushBackend().send(tokens=["x"], title="t", body="b", data={})


def test_push_send_uses_locmem_backend_in_tests() -> None:
    report = push_send(tokens=["a", "b"], title="t", body="b", data={"x": "1"})

    assert report == PushReport(sent=2, failed=0)
    assert len(push_backends.outbox) == 1
    assert push_backends.outbox[0].tokens == ("a", "b")


class InvalidTokenPushBackend:
    """Test double: every token comes back invalid (drives prune tests)."""

    def send(
        self,
        *,
        tokens: Sequence[str],
        title: str,
        body: str,
        data: Mapping[str, str],
    ) -> PushReport:
        return PushReport(sent=0, failed=len(tokens), invalid_tokens=tuple(tokens))
