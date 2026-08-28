"""Meta WhatsApp Cloud API - connector placeholder (by product decision).

The channel plumbing (enum, catalog templates, delivery rows, status
webhook) is first-class today; the actual Graph API call lands here in a
follow-up PR. Until then, an environment that force-enables the channel
fails loudly instead of silently dropping messages.

When implementing: POST
``https://graph.facebook.com/v23.0/{WHATSAPP_PHONE_NUMBER_ID}/messages``
via ``apps.common.http.request_json`` with Bearer WHATSAPP_ACCESS_TOKEN,
``type="template"`` and the ordered body parameters; success predicate is an
allowlist on ``messages[0].id`` (a 2xx with an error body is a failure);
return ``WhatsAppResult(message_id=...)`` - delivery-status webhooks key on
that id.
"""

from collections.abc import Sequence

from apps.notifications.clients.whatsapp.base import WhatsAppNotConfiguredError
from apps.notifications.clients.whatsapp.base import WhatsAppResult


class MetaWhatsAppBackend:
    def send_template(
        self,
        *,
        to: str,
        template_name: str,
        language: str,
        variables: Sequence[str],
    ) -> WhatsAppResult:
        msg = "Meta Cloud API connector is not implemented yet"
        raise WhatsAppNotConfiguredError(msg)
