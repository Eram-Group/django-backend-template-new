"""Write-side for NotificationKindConfig - the config screen's one seam.

The model's clean() holds the field invariants (channels ⊆ supported,
placeholders ⊆ context_keys, copy in both languages); this service adds the
one rule the model cannot see alone: authored_per_send kinds never take
message edits - the broadcast composer owns that copy.
"""

from collections.abc import Sequence

from django.utils import translation
from django.utils.translation import gettext_lazy as _

from apps.notifications.catalog import CATALOG
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import NotificationConfigError
from apps.notifications.models import NotificationKindConfig
from apps.notifications.selectors import notification_config_get


def notification_config_seed() -> list[NotificationKind]:
    """Create the row of every kind that has none; returns the kinds created.

    Seed values come from the catalog, in English for BOTH language columns
    (operators localize in the admin - see ``manage.py seed_notification_config``).
    Existing rows are never touched.
    """
    existing = set(NotificationKindConfig.objects.values_list("kind", flat=True))
    created = []
    with translation.override("en"):
        for kind, entry in CATALOG.items():
            if kind in existing:
                continue
            title, body = str(entry.title), str(entry.body)
            config = NotificationKindConfig(
                kind=kind,
                channels=sorted(str(channel) for channel in entry.default_channels),
                title_ar=title,
                title_en=title,
                body_ar=body,
                body_en=body,
            )
            config.full_clean()
            config.save()
            created.append(kind)
    return created


def notification_config_update(
    *,
    kind: NotificationKind,
    channels: Sequence[str],
    title_ar: str | None = None,
    title_en: str | None = None,
    body_ar: str | None = None,
    body_en: str | None = None,
) -> NotificationKindConfig:
    """Update one kind's channels and copy; None leaves that column alone."""
    entry = catalog_entry(kind)
    config = notification_config_get(kind=kind)
    edits = {
        field: value
        for field, value in {
            "title_ar": title_ar,
            "title_en": title_en,
            "body_ar": body_ar,
            "body_en": body_en,
        }.items()
        if value is not None
    }
    if entry.authored_per_send and any(
        value != getattr(config, field) for field, value in edits.items()
    ):
        raise NotificationConfigError(
            str(
                _(
                    "This action's message is authored per broadcast - "
                    "compose it from the Broadcasts page."
                )
            )
        )
    config.channels = sorted({str(channel) for channel in channels})
    if not entry.authored_per_send:
        for field, value in edits.items():
            setattr(config, field, value)
    config.full_clean()
    config.save()
    return config
