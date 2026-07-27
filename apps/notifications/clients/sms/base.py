"""SMS transport contract + error taxonomy (leaf - importable everywhere)."""

from collections.abc import Sequence
from typing import Protocol


class SmsError(Exception):
    """Base for SMS delivery failures."""


class SmsNotConfiguredError(SmsError):
    """Provider credentials are absent from the environment."""


class SmsProviderError(SmsError):
    """The provider rejected the send - including error-in-2xx-body.

    Bulk providers report counts, not per-number outcomes, so a rejection
    fails the WHOLE group it was raised for - callers mark every delivery in
    that group failed rather than guessing which numbers went through.
    """

    def __init__(self, *, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


class SmsBackend(Protocol):
    """Anything that can deliver ONE body to MANY E164 numbers."""

    def send_many(self, *, to: Sequence[str], body: str) -> None: ...
