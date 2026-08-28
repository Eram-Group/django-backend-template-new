"""SMS transport contract + error taxonomy (leaf - importable everywhere)."""

from collections.abc import Sequence
from typing import Protocol


class SmsError(Exception):
    """Base for SMS delivery failures."""


class SmsNotConfiguredError(SmsError):
    """Provider credentials are absent from the environment."""


class SmsProviderError(SmsError):
    """The provider rejected the send - including error-in-2xx-body.

    ``sent`` names the numbers a provider had ALREADY accepted before the
    rejection: SMSMisr posts one number at a time, and the routing backend
    runs one provider after another. Callers mark exactly those SENT and only
    the rest FAILED - a retry must never re-bill a number that went through.
    Bulk providers report counts, not per-number outcomes, so a rejected bulk
    group carries an empty ``sent`` and fails whole.
    """

    def __init__(self, *, provider: str, detail: str, sent: Sequence[str] = ()) -> None:
        self.provider = provider
        self.detail = detail
        self.sent = tuple(sent)
        super().__init__(f"{provider}: {detail}")


class SmsBackend(Protocol):
    """Anything that can deliver ONE body to MANY E164 numbers."""

    def send_many(self, *, to: Sequence[str], body: str) -> None: ...
