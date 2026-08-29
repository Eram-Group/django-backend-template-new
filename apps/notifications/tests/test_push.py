"""FCM backend: chunking, per-token results, dead-token flags, config guard;
the ``_backend`` seam the outboxes fixture swaps."""

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest
from firebase_admin import messaging

from apps.notifications.clients.push import fcm as fcm_module
from apps.notifications.clients.push import push_send_many
from apps.notifications.clients.push.base import PushMessage
from apps.notifications.clients.push.base import PushNotConfiguredError
from apps.notifications.clients.push.base import PushResult
from apps.notifications.clients.push.fcm import FcmPushBackend
from apps.notifications.tests.locmem import push_outbox


@pytest.fixture
def fake_firebase(monkeypatch: pytest.MonkeyPatch) -> list[list[messaging.Message]]:
    """Bypass credentials and capture every send_each batch.

    Tokens prefixed ``dead-`` answer UnregisteredError, ``mismatch-`` answer
    SenderIdMismatchError, ``flaky-`` a transient QuotaExceededError.
    """
    batches: list[list[messaging.Message]] = []

    def fake_send_each(
        messages: list[messaging.Message], app: Any = None
    ) -> SimpleNamespace:
        batches.append(messages)
        responses = []
        for message in messages:
            if message.token.startswith("dead-"):
                exception: Exception | None = messaging.UnregisteredError("token gone")
            elif message.token.startswith("mismatch-"):
                exception = messaging.SenderIdMismatchError("wrong sender")
            elif message.token.startswith("flaky-"):
                exception = messaging.QuotaExceededError("try later")
            else:
                exception = None
            responses.append(
                SimpleNamespace(success=exception is None, exception=exception)
            )
        return SimpleNamespace(responses=responses)

    monkeypatch.setattr(fcm_module, "_firebase_app", lambda: None)
    monkeypatch.setattr(messaging, "send_each", fake_send_each)
    return batches


def _messages(tokens: Sequence[str]) -> list[PushMessage]:
    return [PushMessage(token=token, title="t", body="b") for token in tokens]


def test_fcm_chunks_at_200(fake_firebase: list[list[messaging.Message]]) -> None:
    results = FcmPushBackend().send_many(
        messages=_messages([f"token-{i}" for i in range(401)])
    )

    assert [len(batch) for batch in fake_firebase] == [200, 200, 1]
    assert len(results) == 401
    assert all(result.ok for result in results)


def test_fcm_per_token_results_flag_dead_tokens(
    fake_firebase: list[list[messaging.Message]],
) -> None:
    results = FcmPushBackend().send_many(
        messages=_messages(["live-1", "dead-1", "mismatch-1", "flaky-1"])
    )

    assert results[0] == PushResult(token="live-1", ok=True)
    assert results[1].invalid is True  # unregistered -> prune
    assert results[2].invalid is True  # sender-id mismatch -> prune
    assert results[3].ok is False
    assert results[3].invalid is False  # transient -> must NOT prune
    assert results[3].detail == "QuotaExceededError"


def test_fcm_without_creds_is_loud() -> None:
    # FIREBASE_CREDENTIALS_B64 is None in test settings; exceptions are not
    # cached by functools.cache, so this stays deterministic.
    with pytest.raises(PushNotConfiguredError):
        FcmPushBackend().send_many(messages=_messages(["x"]))


def test_push_send_many_goes_through_the_swapped_seam() -> None:
    """The outboxes fixture points ``_backend`` at the locmem transport."""
    messages = [
        PushMessage(token="a", title="t", body="b", data={"x": "1"}),
        PushMessage(token="b", title="t", body="b"),
    ]

    results = push_send_many(messages=messages)

    assert results == (
        PushResult(token="a", ok=True),
        PushResult(token="b", ok=True),
    )
    assert push_outbox == messages


class InvalidTokenPushBackend:
    """Test double: every token comes back invalid (drives prune tests)."""

    def send_many(self, *, messages: Sequence[PushMessage]) -> tuple[PushResult, ...]:
        return tuple(
            PushResult(token=m.token, ok=False, invalid=True, detail="gone")
            for m in messages
        )
