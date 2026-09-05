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
- ``title``/``body``/``default_channels``: RECOMMENDED values only - what
  the Notification actions page writes into a kind's config row when it
  opens and finds none (and what the test reset writes); edits here do not
  change a database that already has its rows.

Entries are append-only: adding a NotificationKind puts its card on the
actions page; removing one requires a data migration for surviving rows.
test_catalog and test_config keep everything in lockstep.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationCategory
from apps.notifications.constants import NotificationKind


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

    @property
    def whatsapp_template(self) -> WhatsAppTemplate:
        """The Meta template of a WHATSAPP-capable kind.

        ``__post_init__`` pairs the template with WHATSAPP support, so a
        WhatsApp delivery row (only ever created for a supported channel)
        always finds one; asking for a kind that cannot send on WhatsApp is
        a programming error.
        """
        if self.whatsapp is None:
            msg = "kind does not support WHATSAPP - no template to send"
            raise LookupError(msg)
        return self.whatsapp


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
        # Every broadcast picks its own channels in the composer; the config
        # row's channels are never consulted for this kind.
        default_channels=frozenset(),
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


def validate_context(
    *, kind: NotificationKind, entry: MessageTemplate, context: dict[str, Any]
) -> None:
    provided = frozenset(context)
    if provided != entry.context_keys:
        missing = sorted(entry.context_keys - provided)
        unexpected = sorted(provided - entry.context_keys)
        msg = (
            f"Context for NotificationKind.{kind.name} must have keys "
            f"{sorted(entry.context_keys)} (missing={missing}, "
            f"unexpected={unexpected})."
        )
        raise ValueError(msg)  # a wrong call site - programming error, no envelope


def kind_config_seed(kind: NotificationKind) -> dict[str, Any]:
    """The row state a kind's NotificationKindConfig starts in: the catalog's
    default channels and its starting copy, English in BOTH language columns
    (operators localize in the admin). Used by the seed migration and by the
    test suite's reset - one source."""
    entry = CATALOG[kind]
    with translation.override("en"):
        title, body = str(entry.title), str(entry.body)
    return {
        "channels": [str(channel) for channel in sorted(entry.default_channels)],
        "title": title,
        "title_ar": title,
        "title_en": title,
        "body": body,
        "body_ar": body,
        "body_en": body,
    }
