"""Channel resolution: a kind's config row decides; no row = inbox-only."""

import pytest

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig
from apps.notifications.selectors import effective_channels

pytestmark = pytest.mark.django_db


def _set_channels(kind: NotificationKind, channels: list[str]) -> None:
    NotificationKindConfig.objects.filter(kind=kind).update(channels=channels)


def test_seeded_rows_mirror_catalog_seed_channels() -> None:
    assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset(
        {Channel.PUSH}
    )
    assert effective_channels(kind=NotificationKind.WELCOME) == frozenset()


def test_empty_channels_is_explicit_inbox_only() -> None:
    _set_channels(NotificationKind.PAYMENT_PAID, [])

    assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset()


def test_an_edited_row_takes_effect_immediately() -> None:
    _set_channels(NotificationKind.WALLET_CREDITED, [Channel.PUSH, Channel.SMS])

    assert effective_channels(kind=NotificationKind.WALLET_CREDITED) == frozenset(
        {Channel.PUSH, Channel.SMS}
    )


def test_channels_scope_to_their_kind() -> None:
    _set_channels(NotificationKind.WALLET_CREDITED, [Channel.SMS])

    assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset(
        {Channel.PUSH}
    )


def test_whatsapp_stays_dormant_until_enabled() -> None:
    """The connector-later decision: supported where it makes sense, seeded
    nowhere - an operator edit is the only way to turn it on."""
    for kind in NotificationKind:
        assert Channel.WHATSAPP not in effective_channels(kind=kind)

    _set_channels(NotificationKind.ANNOUNCEMENT, [Channel.PUSH, Channel.WHATSAPP])

    assert Channel.WHATSAPP in effective_channels(kind=NotificationKind.ANNOUNCEMENT)


def test_a_channel_the_kind_no_longer_supports_is_dropped() -> None:
    """A row written before a channel was withdrawn from the kind's supported
    set must not resurrect it - the intersection silently drops it."""
    _set_channels(NotificationKind.WELCOME, [Channel.PUSH, Channel.SMS])

    # WELCOME supports push only; the stored "sms" is ignored, not an error.
    assert effective_channels(kind=NotificationKind.WELCOME) == frozenset(
        {Channel.PUSH}
    )


def test_a_missing_config_row_is_inbox_only() -> None:
    NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).delete()

    assert effective_channels(kind=NotificationKind.WELCOME) == frozenset()
