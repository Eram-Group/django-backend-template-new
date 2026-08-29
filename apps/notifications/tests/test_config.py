"""Channel resolution: one explicit config row per kind - no fallback layer."""

from io import StringIO

import pytest
from django.core.management import call_command

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig
from apps.notifications.selectors import effective_channels

pytestmark = pytest.mark.django_db


def _set_channels(kind: NotificationKind, channels: list[str]) -> None:
    NotificationKindConfig.objects.filter(kind=kind).update(channels=channels)


def test_every_kind_has_exactly_one_config_row() -> None:
    """The no-fallback deal: rows ARE the policy, so all of them must exist
    (the release step seeds them; the conftest reset restores them)."""
    kinds = list(NotificationKindConfig.objects.values_list("kind", flat=True))

    assert sorted(kinds) == sorted(NotificationKind.values)


def test_seed_command_creates_only_missing_rows_from_catalog() -> None:
    """Release-step idempotency: a fresh database gets every row, an operator
    edit on an existing row survives the next deploy."""
    _set_channels(NotificationKind.WELCOME, [Channel.PUSH])
    NotificationKindConfig.objects.filter(kind=NotificationKind.PAYMENT_PAID).delete()
    out = StringIO()

    call_command("seed_notification_config", stdout=out)

    assert out.getvalue().strip() == "created: PAYMENT_PAID"
    seeded = NotificationKindConfig.objects.get(kind=NotificationKind.PAYMENT_PAID)
    assert seeded.channels == ["push"]
    assert seeded.title_en == seeded.title_ar == "Payment received"
    assert effective_channels(kind=NotificationKind.WELCOME) == frozenset(
        {Channel.PUSH}
    )
    assert call_command("seed_notification_config", stdout=StringIO()) is None
    assert NotificationKindConfig.objects.count() == len(NotificationKind)


def test_seeded_rows_mirror_catalog_seed_channels() -> None:
    assert effective_channels(kind=NotificationKind.ANNOUNCEMENT) == frozenset(
        {Channel.PUSH}
    )
    assert effective_channels(kind=NotificationKind.WELCOME) == frozenset()


def test_empty_channels_is_explicit_inbox_only() -> None:
    _set_channels(NotificationKind.PAYMENT_PAID, [])

    assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset()


def test_an_edited_row_takes_effect_immediately() -> None:
    _set_channels(NotificationKind.ANNOUNCEMENT, [Channel.PUSH, Channel.SMS])

    assert effective_channels(kind=NotificationKind.ANNOUNCEMENT) == frozenset(
        {Channel.PUSH, Channel.SMS}
    )


def test_channels_scope_to_their_kind() -> None:
    _set_channels(NotificationKind.ANNOUNCEMENT, [Channel.SMS])

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


def test_a_missing_config_row_fails_loudly() -> None:
    NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).delete()

    with pytest.raises(LookupError, match="WELCOME"):
        effective_channels(kind=NotificationKind.WELCOME)
