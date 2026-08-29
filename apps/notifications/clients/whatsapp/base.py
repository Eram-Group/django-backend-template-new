"""WhatsApp transport contract + error taxonomy (leaf - importable everywhere).

Business-initiated WhatsApp messages have NO free-text form - Meta only
accepts pre-approved templates, hosted (with their per-language bodies) in
Meta Business Manager. Locally we hold the template NAME, a language code,
and ordered variable values; that is why the protocol is ``send_template``
and nothing else.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class WhatsAppError(Exception):
    """Base for WhatsApp delivery failures."""


class WhatsAppNotConfiguredError(WhatsAppError):
    """The Meta connector is not implemented yet - nothing can send."""


class WhatsAppProviderError(WhatsAppError):
    """The provider rejected the message - including error-in-2xx-body."""

    def __init__(self, *, detail: str) -> None:
        self.detail = detail
        super().__init__(f"whatsapp: {detail}")


@dataclass(frozen=True, slots=True)
class WhatsAppResult:
    """Outcome of one template send - the id delivery-status webhooks key on."""

    message_id: str


class WhatsAppBackend(Protocol):
    def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: Sequence[str],
    ) -> WhatsAppResult: ...
