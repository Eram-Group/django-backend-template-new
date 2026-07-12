"""SMS transport contract + error taxonomy (leaf - importable everywhere)."""

from typing import Protocol


class SmsError(Exception):
    """Base for SMS delivery failures."""


class SmsNotConfiguredError(SmsError):
    """Provider credentials are absent from the environment."""


class SmsProviderError(SmsError):
    """The provider rejected the message - including error-in-2xx-body."""

    def __init__(self, *, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


class SmsBackend(Protocol):
    """Anything that can deliver one SMS to one E164 number."""

    def send(self, *, to: str, body: str) -> None: ...
