"""Push transport contract + error taxonomy (leaf - importable everywhere)."""

from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from typing import Protocol


class PushError(Exception):
    """Base for push delivery failures."""


class PushNotConfiguredError(PushError):
    """FIREBASE_CREDENTIALS_B64 is absent from the environment."""


@dataclass(frozen=True, slots=True)
class PushMessage:
    """One rendered message for one device token."""

    token: str
    title: str
    body: str
    data: Mapping[str, str] = field(default_factory=dict)  # FCM data is str-to-str


@dataclass(frozen=True, slots=True)
class PushResult:
    """Outcome for one PushMessage, aligned with the input order.

    ``invalid`` strictly means the TOKEN is dead (FCM unregistered / sender-id
    mismatch) - callers prune that Device row. A transient failure is
    ``ok=False, invalid=False`` and must NOT prune.
    """

    token: str
    ok: bool
    invalid: bool = False
    detail: str = ""


class PushBackend(Protocol):
    def send_many(
        self, *, messages: Sequence[PushMessage]
    ) -> tuple[PushResult, ...]: ...
