"""Message resolution - config rows are the single source of send copy.

Every kind's title/body live on its NotificationKindConfig row (per-language
columns via modeltranslation); rendering formats them with the notification's
context under the ACTIVE locale - callers set it first (delivery tasks use
translation.override(recipient.language); the API renders under the request
locale). Rendered text is never stored - that would freeze one language.

There is no code fallback: a missing row is a deploy error and raises
immediately. Load the map ONCE per executor batch / API request and pass it
via ``configs=`` - never cache across requests (a live admin edit must be
authoritative immediately, and workers are separate processes).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig

ConfigMap = Mapping[NotificationKind, NotificationKindConfig]

_MISSING_ROW_HINT = (
    "seeded by migration 0005; a new NotificationKind must seed its row "
    "in the same change (test_config enforces the pairing)"
)


def notification_config_map() -> dict[NotificationKind, NotificationKindConfig]:
    """All config rows in ONE query - the batch/request-scoped copy source."""
    configs = {
        NotificationKind(row.kind): row for row in NotificationKindConfig.objects.all()
    }
    missing = [kind.name for kind in NotificationKind if kind not in configs]
    if missing:
        msg = (
            f"NotificationKindConfig rows missing for: {', '.join(missing)} "
            f"({_MISSING_ROW_HINT})."
        )
        raise LookupError(msg)
    return configs


def notification_config_get(
    *, kind: NotificationKind, configs: ConfigMap | None = None
) -> NotificationKindConfig:
    if configs is not None:
        row = configs.get(kind)
    else:
        row = NotificationKindConfig.objects.filter(kind=kind).first()
    if row is None:
        msg = (
            f"NotificationKindConfig row missing for {kind.name} ({_MISSING_ROW_HINT})."
        )
        raise LookupError(msg)
    return row


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    title: str
    body: str


def notification_render(
    *,
    kind: NotificationKind,
    context: Mapping[str, Any],
    configs: ConfigMap | None = None,
) -> RenderedMessage:
    """Render one message under the ACTIVE locale - callers set it first."""
    config = notification_config_get(kind=kind, configs=configs)
    return RenderedMessage(
        title=config.title.format(**context),
        body=config.body.format(**context),
    )
