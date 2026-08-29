"""The single-page notification-actions editor and its save seam.

The page is Django's changelist underneath (gates keep exercising it); these
tests cover what the replacement adds: the cards, the one JSON save for
every edited card (atomic, routed through services.notification_config_update),
and the locks.
"""

import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import translation

from apps.notifications import services
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


def _message(kind: str, **fields: Any) -> dict[str, Any]:
    """A complete card payload: the stored copy plus the given overrides."""
    config = NotificationKindConfig.objects.get(kind=kind)
    return _payload(
        kind,
        title_ar=config.title_ar,
        title_en=config.title_en,
        body_ar=config.body_ar,
        body_en=config.body_en,
        **fields,
    )


class TestEditorPage:
    def test_renders_one_card_per_kind(self, client: Client) -> None:
        response = _superuser_client(client).get(reverse(PAGE_URL))

        assert response.status_code == 200
        body = response.content.decode()
        for kind in NotificationKind:
            editable = not CATALOG[kind].authored_per_send
            assert (f'data-kind="{kind}"' in body) is editable

    def test_editable_card_exposes_both_language_columns(self, client: Client) -> None:
        body = _superuser_client(client).get(reverse(PAGE_URL)).content.decode()

        assert 'name="welcome-title_ar"' in body
        assert 'name="welcome-title_en"' in body
        assert 'name="welcome-body_ar"' in body
        assert 'name="welcome-body_en"' in body

    def test_opening_the_page_creates_missing_rows_with_recommended_values(
        self, client: Client
    ) -> None:
        """No seed step: entering the page is the setup. The new card is
        flagged on that visit only."""
        NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).delete()
        admin_client = _superuser_client(client)

        body = admin_client.get(reverse(PAGE_URL)).content.decode()

        created = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
        assert created.channels == []  # WELCOME recommends inbox-only
        assert created.title_en == created.title_ar == "Welcome!"
        assert 'data-kind="welcome" data-new="1"' in body
        assert 'data-kind="payment_paid" data-new="1"' not in body
        again = admin_client.get(reverse(PAGE_URL)).content.decode()
        assert 'data-new="1"' not in again

    def test_a_retired_kind_does_not_appear(self, client: Client) -> None:
        """Removing a kind from the enum needs no migration: its leftover row
        is ignored and the page shows only the current catalog."""
        NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).update(
            kind="retired_kind"
        )

        response = _superuser_client(client).get(reverse(PAGE_URL))

        assert response.status_code == 200
        assert "retired_kind" not in response.content.decode()

    def test_announcement_has_no_card(self, client: Client) -> None:
        """authored_per_send: message and channels are both picked per
        broadcast in the composer - nothing to configure here."""
        body = _superuser_client(client).get(reverse(PAGE_URL)).content.decode()

        assert "announcement-" not in body

    def test_every_catalog_context_key_has_an_example_value(self) -> None:
        """Insert chips (tooltip and "{" menu) need an example per placeholder."""
        from apps.notifications.admin.notificationkindconfig.form import SAMPLE_VALUES

        keys = set().union(*(entry.context_keys for entry in CATALOG.values()))

        assert keys <= set(SAMPLE_VALUES), sorted(keys - set(SAMPLE_VALUES))

    def test_message_fields_render_as_token_editors(self, client: Client) -> None:
        """Each language pane pairs the real field with its editor; the
        palette carries an example value per variable."""
        body = _superuser_client(client).get(reverse(PAGE_URL)).content.decode()

        assert 'name="wallet_credited-body_en"' in body
        assert 'data-for="wallet_credited-body_en"' in body
        assert 'data-for="wallet_credited-body_ar"' in body
        assert 'data-key="balance" data-sample="1,250.00"' in body
        assert "{#" not in body  # a template comment leaking into the page

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
        assert response.json() == {"ok": True, "saved": ["wallet_credited"]}
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
        assert "surname" in response.json()["errors"]["welcome"]["body_en"][0]

    def test_unsupported_channel_is_rejected(self, client: Client) -> None:
        """WELCOME supports push only - the form's choices are the ceiling."""
        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload("welcome", channels=[Channel.SMS]),
        )

        assert response.status_code == 400
        assert "channels" in response.json()["errors"]["welcome"]

    def test_first_save_creates_the_row(self, client: Client) -> None:
        NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).delete()

        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload(
                "welcome",
                channels=[Channel.PUSH],
                title_ar="أهلاً",
                title_en="Hello",
                body_ar="مرحباً {name}",
                body_en="Hi {name}",
            ),
        )

        assert response.status_code == 200
        created = NotificationKindConfig.objects.get(kind=NotificationKind.WELCOME)
        assert created.channels == ["push"]
        assert created.body_ar == "مرحباً {name}"

    def test_a_new_row_is_validated_like_an_existing_one(self, client: Client) -> None:
        NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).delete()

        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload(
                "welcome",
                channels=[Channel.SMS],
                title_ar="a",
                title_en="a",
                body_ar="b",
                body_en="b",
            ),
        )

        assert response.status_code == 400
        assert "channels" in response.json()["errors"]["welcome"]
        assert not NotificationKindConfig.objects.filter(
            kind=NotificationKind.WELCOME
        ).exists()

    def test_several_cards_save_in_one_request(self, client: Client) -> None:
        payload = _message("welcome", channels=[Channel.PUSH])
        payload.update(_message("payment_paid", channels=[]))
        payload["kind"] = ["welcome", "payment_paid"]

        response = _superuser_client(client).post(reverse(SAVE_URL), payload)

        assert response.status_code == 200
        assert response.json() == {"ok": True, "saved": ["welcome", "payment_paid"]}
        assert effective_channels(kind=NotificationKind.WELCOME) == frozenset(
            {Channel.PUSH}
        )
        assert effective_channels(kind=NotificationKind.PAYMENT_PAID) == frozenset()

    def test_one_invalid_card_saves_nothing(self, client: Client) -> None:
        """All or nothing: the valid card must not land while the other fails."""
        payload = _message("welcome", channels=[Channel.PUSH])
        payload.update(_message("payment_paid", channels=[Channel.WHATSAPP]))
        payload["kind"] = ["welcome", "payment_paid"]

        response = _superuser_client(client).post(reverse(SAVE_URL), payload)

        assert response.status_code == 400
        assert list(response.json()["errors"]) == ["payment_paid"]
        assert effective_channels(kind=NotificationKind.WELCOME) == frozenset()

    def test_an_empty_save_is_rejected(self, client: Client) -> None:
        response = _superuser_client(client).post(reverse(SAVE_URL), {})

        assert response.status_code == 400

    def test_unknown_kind_is_rejected(self, client: Client) -> None:
        response = _superuser_client(client).post(
            reverse(SAVE_URL), {"kind": "carrier_pigeon"}
        )

        assert response.status_code == 400

    def test_an_authored_kind_cannot_be_saved_here(self, client: Client) -> None:
        """No card, no save: a hand-crafted POST for the composer's kind is
        refused like an unknown one, so the passthrough template stays."""
        response = _superuser_client(client).post(
            reverse(SAVE_URL),
            _payload(
                "announcement",
                channels=[Channel.SMS],
                title_en="hijacked",
                body_en="hijacked",
            ),
        )

        assert response.status_code == 400
        config = NotificationKindConfig.objects.get(kind=NotificationKind.ANNOUNCEMENT)
        assert config.title_en == "{title}"
        assert config.body_en == "{message}"
        assert config.channels == []

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
