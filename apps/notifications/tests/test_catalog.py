"""The catalog gate: CATALOG and NotificationKind stay in lockstep."""

import string

import pytest
from django.utils import translation

from apps.notifications.catalog import CATALOG
from apps.notifications.catalog import catalog_entry
from apps.notifications.catalog import notification_render
from apps.notifications.constants import NotificationKind


def _placeholders(text: str) -> set[str]:
    return {
        name for _literal, name, _spec, _conv in string.Formatter().parse(text) if name
    }


def test_every_kind_has_a_catalog_entry() -> None:
    missing = [kind for kind in NotificationKind if kind not in CATALOG]
    assert not missing, f"kinds without catalog entries: {missing}"


def test_template_placeholders_match_context_keys() -> None:
    for kind, entry in CATALOG.items():
        found = _placeholders(str(entry.title)) | _placeholders(str(entry.body))
        assert found == entry.context_keys, kind


def test_render_with_exact_context_keys_succeeds_in_both_locales() -> None:
    for kind, entry in CATALOG.items():
        context = dict.fromkeys(entry.context_keys, "placeholder")
        for language in ("ar", "en"):
            with translation.override(language):
                message = notification_render(kind=kind, context=context)
            assert message.title, kind
            assert message.body, kind


def test_missing_catalog_entry_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(CATALOG, NotificationKind.WELCOME)

    with pytest.raises(LookupError, match="WELCOME has no catalog entry"):
        catalog_entry(NotificationKind.WELCOME)
