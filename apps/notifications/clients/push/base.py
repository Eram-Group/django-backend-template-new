from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class PushError(Exception):
    """Base for push delivery failures."""


class PushNotConfiguredError(PushError):
    """FIREBASE_CREDENTIALS_B64 is absent from the environment."""


@dataclass(frozen=True, slots=True)
class PushReport:
    """Outcome of one multicast send."""

    sent: int
    failed: int
    invalid_tokens: tuple[str, ...] = ()  # callers prune these Device rows


class PushBackend(Protocol):
    def send(
        self,
        *,
        tokens: Sequence[str],
        title: str,
        body: str,
        data: Mapping[str, str],  # FCM data payloads are string-to-string
    ) -> PushReport: ...
