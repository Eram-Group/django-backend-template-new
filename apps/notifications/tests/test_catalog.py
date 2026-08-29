"""The catalog gate: CATALOG, NotificationKind, and channel policy in lockstep."""

import string

import pytest

from apps.notifications.catalog import CATALOG
from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import WhatsAppTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationCategory
from apps.notifications.constants import NotificationKind


def _placeholders(text: str) -> set[str]:
    return {
        name for _literal, name, _spec, _conv in string.Formatter().parse(text) if name
    }


def test_every_kind_has_a_catalog_entry() -> None:
    missing = [kind for kind in NotificationKind if kind not in CATALOG]
    assert not missing, f"kinds without catalog entries: {missing}"


def test_seed_placeholders_match_context_keys() -> None:
    """The seed copy respects the same contract config rows are validated
    against - a fresh row starts placeholder-clean."""
    for kind, entry in CATALOG.items():
        found = _placeholders(str(entry.title)) | _placeholders(str(entry.body))
        assert found == entry.context_keys, kind


def test_whatsapp_template_accessor_is_loud_for_non_whatsapp_kinds() -> None:
    assert catalog_entry(NotificationKind.ANNOUNCEMENT).whatsapp_template.name
    with pytest.raises(LookupError, match="WHATSAPP"):
        _ = catalog_entry(NotificationKind.WELCOME).whatsapp_template


def test_only_announcement_is_authored_per_send() -> None:
    """The composer is the only per-send authoring surface; every other kind's
    message is operator-editable config, not per-send input."""
    authored = {kind for kind, entry in CATALOG.items() if entry.authored_per_send}
    assert authored == {NotificationKind.ANNOUNCEMENT}


def test_missing_catalog_entry_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(CATALOG, NotificationKind.WELCOME)

    with pytest.raises(LookupError, match="WELCOME has no catalog entry"):
        catalog_entry(NotificationKind.WELCOME)


# --- MessageTemplate self-validation (import-time guards) ---------------------


def test_default_channels_must_be_subset_of_supported() -> None:
    with pytest.raises(ValueError, match="subset of supported_channels"):
        MessageTemplate(
            title="t",
            body="b",
            category=NotificationCategory.TRANSACTIONAL,
            supported_channels=frozenset({Channel.PUSH}),
            default_channels=frozenset({Channel.SMS}),
        )


def test_whatsapp_template_required_iff_supported() -> None:
    with pytest.raises(ValueError, match="required iff WHATSAPP"):
        MessageTemplate(
            title="t",
            body="b",
            category=NotificationCategory.MARKETING,
            supported_channels=frozenset({Channel.WHATSAPP}),
            default_channels=frozenset(),
        )
    with pytest.raises(ValueError, match="required iff WHATSAPP"):
        MessageTemplate(
            title="t",
            body="b",
            category=NotificationCategory.MARKETING,
            supported_channels=frozenset({Channel.PUSH}),
            default_channels=frozenset(),
            whatsapp=WhatsAppTemplate(name="orphan"),
        )


def test_whatsapp_variables_must_come_from_context_keys() -> None:
    with pytest.raises(ValueError, match="subset of context_keys"):
        MessageTemplate(
            title="t",
            body="{message}",
            category=NotificationCategory.MARKETING,
            supported_channels=frozenset({Channel.WHATSAPP}),
            default_channels=frozenset(),
            context_keys=frozenset({"message"}),
            whatsapp=WhatsAppTemplate(name="a", variables=("other",)),
        )
