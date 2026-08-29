"""Write-side for NotificationKindConfig - the config screen's one seam.

A kind's row is created the first time the actions page is opened (with the
catalog's recommended values - there is no seed step); after that the same
call updates it from the card. The model's clean()
holds the field invariants (channels ⊆ supported, placeholders ⊆
context_keys, copy in both languages); this service adds the one rule the
model cannot see alone: authored_per_send kinds never take message edits -
the broadcast composer owns that copy.
"""

from collections.abc import Sequence

from django.utils.translation import gettext_lazy as _

from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import NotificationConfigError
from apps.notifications.models import NotificationKindConfig


def notification_config_update(
    *,
    kind: NotificationKind,
    channels: Sequence[str],
    title_ar: str | None = None,
    title_en: str | None = None,
    body_ar: str | None = None,
    body_en: str | None = None,
) -> NotificationKindConfig:
    """Create or update one kind's channels and copy; None leaves a column
    alone (a new row needs all four copy columns - the model enforces it)."""
    entry = catalog_entry(kind)
    config = NotificationKindConfig.objects.filter(kind=kind).first()
    if config is None:
        config = NotificationKindConfig(kind=kind)
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
