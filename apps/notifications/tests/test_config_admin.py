"""The notification-actions admin: a standard change form per kind.

Rows exist by migration (one per catalog kind); the change form edits the
channel policy (a checkbox set limited to the kind's supported channels)
and the ar/en copy on translation tabs, shows the placeholders and a
sample-rendered preview, and writes through
services.notification_config_update - the single writer.
"""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.notifications import services
from apps.notifications.admin.forms import SAMPLE_VALUES
from apps.notifications.catalog import CATALOG
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import NotificationConfigError
from apps.notifications.models import NotificationKindConfig
from apps.notifications.selectors import effective_channels
from apps.notifications.selectors import notification_config_map
from apps.notifications.selectors import notification_render
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LIST_URL = "admin:notifications_notificationkindconfig_changelist"


def _superuser_client(client: Client) -> Client:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    return client


def _change_url(kind: NotificationKind) -> str:
    config = NotificationKindConfig.objects.get(kind=kind)
    return reverse(
        "admin:notifications_notificationkindconfig_change", args=[config.pk]
    )


def _payload(kind: NotificationKind, **fields: Any) -> dict[str, Any]:
    """A complete change-form POST: the stored copy plus the overrides."""
    config = NotificationKindConfig.objects.get(kind=kind)
    payload: dict[str, Any] = {
        "channels": list(config.channels),
        "title_ar": config.title_ar,
        "title_en": config.title_en,
        "body_ar": config.body_ar,
        "body_en": config.body_en,
        "_save": "Save",
    }
    payload.update(fields)
    return payload


class TestChangeForm:
    def test_every_catalog_kind_has_a_row_to_edit(self, client: Client) -> None:
        """Rows are born from the seed migration - the changelist lists one
        per kind, and there is no add button to make more."""
        response = _superuser_client(client).get(reverse(LIST_URL))

        assert response.context["cl"].result_count == len(NotificationKind)
        assert response.context["has_add_permission"] is False

    def test_renders_both_language_columns_and_the_supported_channels(
        self, client: Client
    ) -> None:
        body = (
            _superuser_client(client)
            .get(_change_url(NotificationKind.WELCOME))
            .content.decode()
        )

        for name in ("title_ar", "title_en", "body_ar", "body_en"):
            assert f'name="{name}"' in body
        # WELCOME supports push only: the picker offers nothing else.
        assert 'name="channels" value="push"' in body
        assert 'value="sms"' not in body

    def test_shows_placeholders_and_a_sample_preview(self, client: Client) -> None:
        body = (
            _superuser_client(client)
            .get(_change_url(NotificationKind.WALLET_CREDITED))
            .content.decode()
        )

        assert "{balance}" in body
        assert SAMPLE_VALUES["balance"] in body  # the preview rendered with it

    def test_announcement_copy_is_read_only(self, client: Client) -> None:
        """authored_per_send: the composer writes the message per broadcast."""
        body = (
            _superuser_client(client)
            .get(_change_url(NotificationKind.ANNOUNCEMENT))
            .content.decode()
        )

        assert 'name="title_en"' not in body
        assert 'name="body_en"' not in body
        assert 'name="channels"' in body  # the channel policy is still editable

    def test_every_catalog_context_key_has_an_example_value(self) -> None:
        """The preview and the placeholder list need an example per key."""
        keys = set().union(*(entry.context_keys for entry in CATALOG.values()))

        assert keys <= set(SAMPLE_VALUES), sorted(keys - set(SAMPLE_VALUES))


class TestChangeFormSave:
    def test_save_updates_channels_and_copy_for_the_next_send(
        self, client: Client
    ) -> None:
        response = _superuser_client(client).post(
            _change_url(NotificationKind.WALLET_CREDITED),
            _payload(
                NotificationKind.WALLET_CREDITED,
                channels=[Channel.PUSH, Channel.SMS],
                title_ar="تم الإيداع",
                title_en="Wallet topped up",
                body_ar="رصيدك الجديد {balance}",
                body_en="Your new balance is {balance}",
            ),
        )

        assert response.status_code == 302
        assert effective_channels(kind=NotificationKind.WALLET_CREDITED) == frozenset(
            {Channel.PUSH, Channel.SMS}
        )
        context = {"amount": "10", "currency": "SAR", "balance": "60.00"}
        configs = notification_config_map()
        with translation.override("en"):
            english = notification_render(
                kind=NotificationKind.WALLET_CREDITED, context=context, configs=configs
            )
        with translation.override("ar"):
            arabic = notification_render(
                kind=NotificationKind.WALLET_CREDITED, context=context, configs=configs
            )
        assert english.title == "Wallet topped up"
        assert english.body == "Your new balance is 60.00"
        assert arabic.body == "رصيدك الجديد 60.00"

    def test_unknown_placeholder_is_a_field_error(self, client: Client) -> None:
        response = _superuser_client(client).post(
            _change_url(NotificationKind.WELCOME),
            _payload(NotificationKind.WELCOME, body_en="Hi {surname}"),
        )

        assert response.status_code == 200
        assert "surname" in response.context["adminform"].form.errors["body_en"][0]

    @pytest.mark.parametrize("body", ["Broken {", "{name:>10}", "{name!r}"])
    def test_unrenderable_template_is_a_field_error(
        self, client: Client, body: str
    ) -> None:
        response = _superuser_client(client).post(
            _change_url(NotificationKind.WELCOME),
            _payload(NotificationKind.WELCOME, body_en=body),
        )

        assert response.status_code == 200
        assert "body_en" in response.context["adminform"].form.errors

    def test_unsupported_channel_cannot_be_posted(self, client: Client) -> None:
        """WELCOME supports push only - the form's choices are the ceiling."""
        response = _superuser_client(client).post(
            _change_url(NotificationKind.WELCOME),
            _payload(NotificationKind.WELCOME, channels=[Channel.SMS]),
        )

        assert response.status_code == 200
        assert "channels" in response.context["adminform"].form.errors
        assert effective_channels(kind=NotificationKind.WELCOME) == frozenset()

    def test_inbox_only_is_a_legitimate_policy(self, client: Client) -> None:
        response = _superuser_client(client).post(
            _change_url(NotificationKind.PAYMENT_PAID),
            _payload(NotificationKind.PAYMENT_PAID, channels=[]),
        )

        assert response.status_code == 302
        assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset()

    def test_an_authored_kind_keeps_its_passthrough_copy(self, client: Client) -> None:
        """The copy fields are read-only on the form; a hand-crafted POST
        that carries them cannot change the passthrough template."""
        response = _superuser_client(client).post(
            _change_url(NotificationKind.ANNOUNCEMENT),
            {
                "channels": [Channel.SMS],
                "title_en": "hijacked",
                "body_en": "hijacked",
                "_save": "Save",
            },
        )

        assert response.status_code == 302
        config = NotificationKindConfig.objects.get(kind=NotificationKind.ANNOUNCEMENT)
        assert config.title_en == "{title}"
        assert config.body_en == "{message}"
        assert config.channels == [Channel.SMS]

    def test_a_staff_user_without_change_permission_is_refused(
        self, client: Client
    ) -> None:
        client.force_login(UserFactory.create(is_staff=True))

        response = client.post(
            _change_url(NotificationKind.WELCOME), _payload(NotificationKind.WELCOME)
        )

        # admin_view bounces a staff user with no model perms at the door.
        assert response.status_code in (302, 403)


class TestConfigService:
    def test_authored_kind_rejects_message_edits(self) -> None:
        expected = translation.gettext(
            "This action's message is authored per broadcast - "
            "compose it from the Broadcasts page."
        )
        with pytest.raises(NotificationConfigError, match=re.escape(expected)):
            services.notification_config_update(
                kind=NotificationKind.ANNOUNCEMENT,
                channels=[Channel.PUSH],
                title_en="Static title",
            )

    def test_authored_kind_still_takes_channel_edits(self) -> None:
        config = services.notification_config_update(
            kind=NotificationKind.ANNOUNCEMENT,
            channels=[Channel.SMS, Channel.PUSH],
        )

        assert config.channels == [Channel.PUSH, Channel.SMS]  # sorted, canonical
        assert config.title_en == "{title}"  # untouched

    def test_unchanged_message_values_do_not_trip_the_lock(self) -> None:
        """Round-tripping the stored copy is a no-op, not a violation."""
        config = services.notification_config_update(
            kind=NotificationKind.ANNOUNCEMENT,
            channels=[Channel.PUSH],
            title_en="{title}",
        )

        assert config.channels == [Channel.PUSH]
