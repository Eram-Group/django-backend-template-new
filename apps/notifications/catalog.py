"""Notification message catalog - the single source of copy per kind.

Rows store (kind, context) and render HERE at send/read time under the
active locale (tasks set translation.override(recipient.language); the API
renders under the request locale). Rendered text is never stored - that
would freeze one language, the old template's bug.

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
from apps.notifications.constants import NotificationKind


@dataclass(frozen=True, slots=True)
class MessageTemplate:
    """Copy + channel policy for one notification kind."""

    title: StrOrPromise  # gettext_lazy - str() at render time = active locale
    body: StrOrPromise  # gettext_lazy with {placeholders} matching context_keys
    channels: frozenset[Channel]  # inbox row always exists; these fan out
    context_keys: frozenset[str] = frozenset()


CATALOG: Mapping[NotificationKind, MessageTemplate] = {
    NotificationKind.WELCOME: MessageTemplate(
        title=_("Welcome!"),
        body=_("Welcome aboard, {name}!"),
        channels=frozenset({Channel.PUSH}),
        context_keys=frozenset({"name"}),
    ),
    NotificationKind.ANNOUNCEMENT: MessageTemplate(
        title=_("Announcement"),
        body="{message}",  # operator-authored full text, passed as context
        channels=frozenset({Channel.PUSH, Channel.SMS}),
        context_keys=frozenset({"message"}),
    ),
    NotificationKind.PAYMENT_PAID: MessageTemplate(
        title=_("Payment received"),
        body=_("Your payment of {amount} {currency} was received."),
        channels=frozenset({Channel.PUSH}),
        context_keys=frozenset({"amount", "currency"}),
    ),
    NotificationKind.WALLET_CREDITED: MessageTemplate(
        title=_("Wallet credited"),
        body=_("{amount} {currency} was added to your wallet. New balance: {balance}."),
        channels=frozenset({Channel.PUSH}),
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
