"""Channel resolution: catalog defaults + admin override pins."""

import pytest

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.selectors import effective_channels
from apps.notifications.tests.factories import NotificationChannelOverrideFactory

pytestmark = pytest.mark.django_db


def test_defaults_apply_with_no_overrides() -> None:
    assert effective_channels(kind=NotificationKind.ANNOUNCEMENT) == frozenset(
        {Channel.PUSH}
    )
    assert effective_channels(kind=NotificationKind.WELCOME) == frozenset()


def test_override_enables_a_supported_non_default_channel() -> None:
    NotificationChannelOverrideFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, channel=Channel.SMS, enabled=True
    )

    assert effective_channels(kind=NotificationKind.ANNOUNCEMENT) == frozenset(
        {Channel.PUSH, Channel.SMS}
    )


def test_override_disables_a_default_channel() -> None:
    NotificationChannelOverrideFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, channel=Channel.PUSH, enabled=False
    )

    assert effective_channels(kind=NotificationKind.ANNOUNCEMENT) == frozenset()


def test_overrides_scope_to_their_kind() -> None:
    NotificationChannelOverrideFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, channel=Channel.SMS, enabled=True
    )

    assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset(
        {Channel.PUSH}
    )


def test_whatsapp_stays_dormant_until_pinned_on() -> None:
    """The connector-later decision: supported everywhere it makes sense,
    default nowhere - an operator pin is the only way to turn it on."""
    for kind in NotificationKind:
        assert Channel.WHATSAPP not in effective_channels(kind=kind)

    NotificationChannelOverrideFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, channel=Channel.WHATSAPP, enabled=True
    )

    assert Channel.WHATSAPP in effective_channels(kind=NotificationKind.ANNOUNCEMENT)
