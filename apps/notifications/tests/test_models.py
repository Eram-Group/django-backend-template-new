"""Model invariants: idempotency constraints and structural guards."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils.translation import gettext

from apps.notifications.catalog import CATALOG
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationDelivery
from apps.notifications.models import NotificationKindConfig
from apps.notifications.tests.factories import BroadcastFactory
from apps.notifications.tests.factories import NotificationDeliveryFactory
from apps.notifications.tests.factories import NotificationFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_delivery_unique_per_notification_and_channel() -> None:
    """THE idempotency key: one delivery row per (notification, channel)."""
    delivery = NotificationDeliveryFactory.create(channel=Channel.PUSH)

    with pytest.raises(IntegrityError):
        NotificationDelivery.objects.create(
            notification=delivery.notification, channel=Channel.PUSH
        )


def test_provider_message_id_unique_when_present() -> None:
    NotificationDeliveryFactory.create(
        channel=Channel.WHATSAPP, provider="whatsapp", provider_message_id="wamid.1"
    )
    other = NotificationDeliveryFactory.create(channel=Channel.SMS)

    with pytest.raises(IntegrityError):
        NotificationDelivery.objects.create(
            notification=other.notification,
            channel=Channel.WHATSAPP,
            provider="whatsapp",
            provider_message_id="wamid.1",
        )


def test_blank_provider_message_ids_do_not_collide() -> None:
    """Push/SMS return no consumable id - blank rows stay unconstrained."""
    first = NotificationDeliveryFactory.create(channel=Channel.PUSH)
    second = NotificationDeliveryFactory.create(channel=Channel.PUSH)

    assert first.provider_message_id == second.provider_message_id == ""


def test_broadcast_recipient_unique_within_a_broadcast() -> None:
    """Dispatch-resume backstop: one inbox row per (broadcast, recipient)."""
    broadcast = BroadcastFactory.create()
    notification = NotificationFactory.create(
        kind=NotificationKind.ANNOUNCEMENT, broadcast=broadcast
    )

    with pytest.raises(IntegrityError):
        NotificationFactory.create(
            kind=NotificationKind.ANNOUNCEMENT,
            broadcast=broadcast,
            recipient=notification.recipient,
        )


def test_single_send_notifications_are_not_constrained_per_recipient() -> None:
    user = UserFactory.create()
    NotificationFactory.create(recipient=user)
    NotificationFactory.create(recipient=user)  # no broadcast -> no constraint


def test_kind_config_rejects_unsupported_channel() -> None:
    """WELCOME supports push only - enabling whatsapp on it is a config error."""
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
    config.channels = [Channel.WHATSAPP]

    with pytest.raises(ValidationError) as excinfo:
        config.full_clean()
    assert excinfo.value.message_dict["channels"] == [
        gettext(
            "%(kind)s does not support: %(channels)s. "
            "Supported channels: %(supported)s."
        )
        % {"kind": "welcome", "channels": "whatsapp", "supported": "push"}
    ]


def test_kind_config_rejects_placeholders_outside_the_contract() -> None:
    """Producers pass exactly context_keys - copy cannot reference more, and
    attribute traversals like {name.__class__} never reach str.format."""
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
    config.body_en = "Hi {surname}!"

    with pytest.raises(ValidationError, match="surname"):
        config.full_clean()


def test_kind_config_requires_copy_in_both_languages() -> None:
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
    config.title_ar = ""

    with pytest.raises(ValidationError, match="title_ar"):
        config.full_clean()


def test_kind_config_with_an_invalid_kind_reports_the_choice_error() -> None:
    """clean() steps aside for a bad choice - clean_fields owns that error."""
    config = NotificationKindConfig(kind="not_a_kind", title="t", body="b")

    with pytest.raises(ValidationError) as excinfo:
        config.full_clean()

    assert set(excinfo.value.message_dict) >= {"kind"}


def test_kind_config_without_a_catalog_entry_is_a_loud_code_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid kind with no catalog entry is not a form error to swallow."""
    monkeypatch.delitem(CATALOG, NotificationKind.WELCOME)
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)

    with pytest.raises(LookupError, match="catalog"):
        config.full_clean()


def test_kind_config_accepts_supported_channels_and_known_placeholders() -> None:
    config = NotificationKindConfig.objects.get(kind=NotificationKind.WALLET_CREDITED)
    config.channels = [Channel.PUSH, Channel.SMS]
    config.body_en = "New balance: {balance}."
    config.body_ar = "الرصيد الجديد: {balance}."

    config.full_clean()  # does not raise


def test_enum_columns_use_callable_choices() -> None:
    """kind/channel carry ``choices=<callable>``: the migration state holds
    the import path, not the members, so a new kind is never a migration -
    while a value outside the enum still fails full_clean."""
    from apps.notifications.constants import channel_choices
    from apps.notifications.constants import notification_kind_choices
    from apps.notifications.models import NotificationDelivery
    from apps.notifications.models import NotificationKindConfig

    kind = NotificationKindConfig._meta.get_field("kind")
    assert kind.deconstruct()[3]["choices"] is notification_kind_choices
    channel = NotificationDelivery._meta.get_field("channel")
    assert channel.deconstruct()[3]["choices"] is channel_choices
    with pytest.raises(ValidationError, match="kind"):
        NotificationKindConfig(kind="carrier_pigeon").full_clean()
    with pytest.raises(ValidationError, match="channel"):
        NotificationDelivery(channel="carrier_pigeon").full_clean(
            exclude=["notification"]
        )


# --- the template grammar is {key} tokens only (review 2026-09-05, #7) --------


@pytest.mark.parametrize(
    "body",
    [
        "Broken {",
        "Broken }",
        "{amount:invalid_format}",
        "{amount!r}",
        "{x:{amount}}",
        "{}",
        "{amount.__class__}",
        "{amount[0]}",
    ],
)
def test_config_rejects_every_template_shape_that_could_fail_to_render(
    body: str,
) -> None:
    config = NotificationKindConfig.objects.get(kind=NotificationKind.PAYMENT_PAID)
    config.body_en = body

    with pytest.raises(ValidationError) as excinfo:
        config.full_clean()

    assert "body_en" in excinfo.value.message_dict


def test_config_accepts_literal_braces_and_known_keys() -> None:
    config = NotificationKindConfig.objects.get(kind=NotificationKind.PAYMENT_PAID)
    config.body_en = "Paid {amount} {currency} {{literally}}"

    config.full_clean()  # no error
