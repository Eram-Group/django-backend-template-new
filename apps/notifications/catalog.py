"""Notification kind catalog - the CODE-SIDE contract per kind.

Runtime behaviour lives on each kind's NotificationKindConfig row (explicit
channel list + admin-editable ar/en copy; resolution in
``selectors.config.effective_channels`` and rendering in
``selectors.messages``). The catalog declares what code guarantees and
validates against:

- ``context_keys``: exactly what producers pass - the placeholder contract
  for admin-authored copy.
- ``supported_channels``: what a kind CAN send on - the ceiling any config
  row or per-broadcast pick is intersected with.
- ``category`` and the WhatsApp template mapping (Meta hosts the approved
  per-language bodies, so entries carry only the template NAME plus the
  ordered context keys that fill its {{1}}, {{2}}, ... slots).
- ``title``/``body``/``default_channels``: SEED values only - what migration
  0004 wrote into each config row and what the test reset restores; edits
  here do not change a database that already has its rows.

Entries are append-only: adding a NotificationKind requires seeding its
config row in the same change; removing one requires a data migration for
surviving rows. test_catalog and test_config keep everything in lockstep.
"""

from collections.abc import Mapping
from dataclasses import dataclass

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
    """Contract + seed values for one notification kind."""

    title: StrOrPromise  # SEED copy - runtime title lives on the config row
    body: StrOrPromise  # SEED copy with {placeholders} matching context_keys
    category: NotificationCategory
    supported_channels: frozenset[Channel]  # what a config row MAY enable
    default_channels: frozenset[Channel]  # SEED channels for the config row
    context_keys: frozenset[str] = frozenset()
    whatsapp: WhatsAppTemplate | None = None  # required iff WHATSAPP supported
    # True = the operator writes the message per send (the broadcast composer);
    # the kind's title/body are passthrough format strings, not editable copy.
    authored_per_send: bool = False

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
        # Both halves are operator-authored and travel in the context. A fixed
        # gettext title would make every announcement read "Announcement" in
        # the tray, which is exactly the line a recipient decides on.
        title="{title}",
        body="{message}",
        category=NotificationCategory.MARKETING,
        supported_channels=frozenset({Channel.PUSH, Channel.SMS, Channel.WHATSAPP}),
        # SMS costs money and Egyptian SMS loops per message; WhatsApp waits
        # on the connector + tier ramp. Operators pin them on per campaign.
        default_channels=frozenset({Channel.PUSH}),
        context_keys=frozenset({"title", "message"}),
        # One variable still: Meta approved this template body with a single
        # {{1}} slot and the slot count is fixed on their side, so carrying the
        # title too would need a new template submitted and re-approved.
        whatsapp=WhatsAppTemplate(name="announcement", variables=("message",)),
        authored_per_send=True,
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
