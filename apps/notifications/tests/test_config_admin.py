"""The single-page notification-actions editor and its save seam.

The page is Django's changelist underneath (gates keep exercising it); these
tests cover what the replacement adds: the cards, the per-card JSON save
routed through services.notification_config_update, and the locks.
"""

from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.notifications import services
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import NotificationConfigError
from apps.notifications.models import NotificationKindConfig
from apps.notifications.selectors import effective_channels
from apps.notifications.selectors import notification_render
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PAGE_URL = "admin:notifications_notificationkindconfig_changelist"
SAVE_URL = "admin:notifications_notificationkindconfig_config_save"


def _superuser_client(client: Client) -> Client:
    client.force_login(UserFactory.create(is_staff=True, is_superuser=True))
    return client


def _payload(kind: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind}
    for name, value in fields.items():
        payload[f"{kind}-{name}"] = value
    return payload


class TestEditorPage:
    def test_renders_one_card_per_kind(self, client: Client) -> None:
        response = _superuser_client(client).get(reverse(PAGE_URL))

        assert response.status_code == 200
        body = response.content.decode()
        for kind in NotificationKind:
            assert f'data-kind="{kind}"' in body

    def test_editable_card_exposes_both_language_columns(self, client: Client) -> None:
        body = _superuser_client(client).get(reverse(PAGE_URL)).content.decode()

        assert 'name="welcome-title_ar"' in body
        assert 'name="welcome-title_en"' in body
        assert 'name="welcome-body_ar"' in body
        assert 'name="welcome-body_en"' in body

    def test_announcement_message_is_locked(self, client: Client) -> None:
        """authored_per_send: channels stay editable, the copy does not."""
        body = _superuser_client(client).get(reverse(PAGE_URL)).content.decode()

        assert 'name="announcement-channels"' in body
        assert 'name="announcement-title_en"' not in body
        assert 'name="announcement-body_en"' not in body

    def test_sort_params_do_not_break_the_page(self, client: Client) -> None:
        """The admin sorting gate hits the changelist with ?o= permutations."""
        response = _superuser_client(client).get(reverse(PAGE_URL), {"o": "1"})

        assert response.status_code == 200


class TestConfigSave:
    def test_save_updates_channels_and_copy_for_the_next_send(
        self, client: Client
    ) -> None:
        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload(
                "wallet_credited",
                channels=[Channel.PUSH, Channel.SMS],
                title_ar="تم الإيداع",
                title_en="Wallet topped up",
                body_ar="رصيدك الجديد {balance}",
                body_en="Your new balance is {balance}",
            ),
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True}
        assert effective_channels(kind=NotificationKind.WALLET_CREDITED) == frozenset(
            {Channel.PUSH, Channel.SMS}
        )
        context = {"amount": "10", "currency": "SAR", "balance": "60.00"}
        with translation.override("en"):
            english = notification_render(
                kind=NotificationKind.WALLET_CREDITED, context=context
            )
        with translation.override("ar"):
            arabic = notification_render(
                kind=NotificationKind.WALLET_CREDITED, context=context
            )
        assert english.title == "Wallet topped up"
        assert english.body == "Your new balance is 60.00"
        assert arabic.body == "رصيدك الجديد 60.00"

    def test_unknown_placeholder_is_a_field_error(self, client: Client) -> None:
        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload(
                "welcome",
                channels=[],
                title_ar="أهلاً",
                title_en="Hello",
                body_ar="مرحباً {name}",
                body_en="Hi {surname}",
            ),
        )

        assert response.status_code == 400
        assert "surname" in response.json()["errors"]["body_en"][0]

    def test_unsupported_channel_is_rejected(self, client: Client) -> None:
        """WELCOME supports push only - the form's choices are the ceiling."""
        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload("welcome", channels=[Channel.SMS]),
        )

        assert response.status_code == 400
        assert "channels" in response.json()["errors"]

    def test_unknown_kind_is_rejected(self, client: Client) -> None:
        response = _superuser_client(client).post(
            reverse(SAVE_URL), {"kind": "carrier_pigeon"}
        )

        assert response.status_code == 400

    def test_announcement_message_fields_cannot_be_smuggled(
        self, client: Client
    ) -> None:
        """The form drops message fields for authored kinds - a hand-crafted
        POST cannot rewrite the passthrough template."""
        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload(
                "announcement",
                channels=[Channel.PUSH],
                title_en="hijacked",
                body_en="hijacked",
            ),
        )

        assert response.status_code == 200
        config = NotificationKindConfig.objects.get(kind=NotificationKind.ANNOUNCEMENT)
        assert config.title_en == "{title}"
        assert config.body_en == "{message}"

    def test_anonymous_callers_are_redirected_to_login(self, client: Client) -> None:
        response = client.post(reverse(SAVE_URL), {})

        assert response.status_code == 302

    def test_a_staff_user_without_change_permission_is_refused(
        self, client: Client
    ) -> None:
        client.force_login(UserFactory.create(is_staff=True))

        response = client.post(reverse(SAVE_URL), {})

        # admin_view bounces a staff user with no model perms at the door.
        assert response.status_code in (302, 403)


class TestConfigService:
    def test_authored_kind_rejects_message_edits(self) -> None:
        with pytest.raises(NotificationConfigError, match="authored per broadcast"):
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
