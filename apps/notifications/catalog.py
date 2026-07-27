"""Notification message catalog - the single source of copy per kind.

Rows store (kind, context) and render HERE at send/read time under the
active locale (delivery tasks set translation.override(recipient.language);
the API renders under the request locale). Rendered text is never stored -
that would freeze one language, the old template's bug.

Channel policy is two-layered: the catalog declares what a kind CAN use
(``supported_channels``) and what it uses out of the box
(``default_channels``); an admin-editable NotificationChannelOverride row
pins one channel on/off at runtime without a deploy. Resolution lives in
``selectors.config.effective_channels``.

WhatsApp has no local copy at all: Meta hosts the approved per-language
template bodies, so entries carry only the template NAME plus the ordered
context keys that fill its {{1}}, {{2}}, ... slots.

Entries are append-only: removing a NotificationKind requires a data
migration for surviving rows of that kind. test_catalog keeps CATALOG and
NotificationKind in lockstep.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationCategory
from apps.notifications.constants import NotificationKind

#: user.language -> the language code sent to Meta with the template name.
WHATSAPP_LANGUAGE_CODES: Mapping[str, str] = {"ar": "ar", "en": "en"}


@dataclass(frozen=True, slots=True)
class WhatsAppTemplate:
    """A Meta-approved template: its name + ordered variable context keys."""

    name: str
    variables: tuple[str, ...] = ()  # context keys, in {{1}}, {{2}}, ... order


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    """Copy + channel policy for one notification kind."""

    title: StrOrPromise  # gettext_lazy - str() at render time = active locale
    body: StrOrPromise  # gettext_lazy with {placeholders} matching context_keys
    category: NotificationCategory
    supported_channels: frozenset[Channel]  # what an override MAY pin on
    default_channels: frozenset[Channel]  # what fans out with no override
    context_keys: frozenset[str] = frozenset()
    whatsapp: WhatsAppTemplate | None = None  # required iff WHATSAPP supported

    def __post_init__(self) -> None:
        if not self.default_channels <= self.supported_channels:
            msg = "default_channels must be a subset of supported_channels"
            raise ValueError(msg)
        if (Channel.WHATSAPP in self.supported_channels) != (self.whatsapp is not None):
            msg = "whatsapp template is required iff WHATSAPP is supported"
            raise ValueError(msg)
        if self.whatsapp is not None and not set(self.whatsapp.variables) <= set(
            self.context_keys
        ):
            msg = "whatsapp variables must be a subset of context_keys"
            raise ValueError(msg)


CATALOG: Mapping[NotificationKind, MessageTemplate] = {
    NotificationKind.WELCOME: MessageTemplate(
        title=_("Welcome!"),
        body=_("Welcome aboard, {name}!"),
        category=NotificationCategory.TRANSACTIONAL,
        supported_channels=frozenset({Channel.PUSH}),
        default_channels=frozenset(),  # inbox-only out of the box
        context_keys=frozenset({"name"}),
    ),
    NotificationKind.ANNOUNCEMENT: MessageTemplate(
        title=_("Announcement"),
        body="{message}",  # operator-authored full text, passed as context
        category=NotificationCategory.MARKETING,
        supported_channels=frozenset({Channel.PUSH, Channel.SMS, Channel.WHATSAPP}),
        # SMS costs money and Egyptian SMS loops per message; WhatsApp waits
        # on the connector + tier ramp. Operators pin them on per campaign.
        default_channels=frozenset({Channel.PUSH}),
        context_keys=frozenset({"message"}),
        whatsapp=WhatsAppTemplate(name="announcement", variables=("message",)),
    ),
    NotificationKind.PAYMENT_PAID: MessageTemplate(
        title=_("Payment received"),
        body=_("Your payment of {amount} {currency} was received."),
        category=NotificationCategory.TRANSACTIONAL,
        supported_channels=frozenset({Channel.PUSH, Channel.SMS}),
        default_channels=frozenset({Channel.PUSH}),
        context_keys=frozenset({"amount", "currency"}),
    ),
    NotificationKind.WALLET_CREDITED: MessageTemplate(
        title=_("Wallet credited"),
        body=_("{amount} {currency} was added to your wallet. New balance: {balance}."),
        category=NotificationCategory.TRANSACTIONAL,
        supported_channels=frozenset({Channel.PUSH, Channel.SMS}),
        default_channels=frozenset({Channel.PUSH}),
        context_keys=frozenset({"amount", "currency", "balance"}),
    ),
}


def catalog_entry(kind: NotificationKind) -> MessageTemplate:
    try:
        return CATALOG[kind]
    except KeyError as exc:
        msg = (
            f"NotificationKind.{kind.name} has no catalog entry - add it to "
            "apps.notifications.catalog.CATALOG in the same change "
            "(test_catalog enforces the pairing)."
        )
        raise LookupError(msg) from exc


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    title: str
    body: str


def notification_render(
    *, kind: NotificationKind, context: Mapping[str, Any]
) -> RenderedMessage:
    """Render one message under the ACTIVE locale - callers set it first."""
    entry = catalog_entry(kind)
    return RenderedMessage(
        title=str(entry.title).format(**context),
        body=str(entry.body).format(**context),
    )
