"""DB-backed message rendering - config rows are the copy source."""

from typing import Any

import pytest
from django.utils import translation

from apps.notifications.catalog import CATALOG
from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig
from apps.notifications.selectors import notification_config_get
from apps.notifications.selectors import notification_config_map
from apps.notifications.selectors import notification_render

pytestmark = pytest.mark.django_db


def test_render_resolves_the_active_language_column() -> None:
    NotificationKindConfig.objects.filter(kind=NotificationKind.WELCOME).update(
        title_ar="أهلاً!",
        title_en="Hello!",
        body_ar="مرحباً {name}",
        body_en="Hi {name}",
    )

    configs = notification_config_map()
    with translation.override("ar"):
        arabic = notification_render(
            kind=NotificationKind.WELCOME, context={"name": "Omar"}, configs=configs
        )
    with translation.override("en"):
        english = notification_render(
            kind=NotificationKind.WELCOME, context={"name": "Omar"}, configs=configs
        )

    assert (arabic.title, arabic.body) == ("أهلاً!", "مرحباً Omar")
    assert (english.title, english.body) == ("Hello!", "Hi Omar")


def test_every_seeded_row_renders_in_both_locales() -> None:
    """The old catalog render lockstep, now against the seeded rows."""
    configs = notification_config_map()
    for kind, entry in CATALOG.items():
        context = dict.fromkeys(entry.context_keys, "placeholder")
        for language in ("ar", "en"):
            with translation.override(language):
                message = notification_render(
                    kind=kind, context=context, configs=configs
                )
            assert message.title, kind
            assert message.body, kind


def test_a_preloaded_config_map_adds_no_queries(
    django_assert_num_queries: Any,
) -> None:
    """The batch contract: executors and the API load the map once."""
    configs = notification_config_map()

    with django_assert_num_queries(0):
        notification_render(
            kind=NotificationKind.WELCOME, context={"name": "x"}, configs=configs
        )


def test_missing_rows_raise_with_seeding_guidance() -> None:
    NotificationKindConfig.objects.all().delete()

    with pytest.raises(LookupError, match="seed"):
        notification_config_map()
    with pytest.raises(LookupError, match="seed"):
        notification_config_get(kind=NotificationKind.WELCOME)
