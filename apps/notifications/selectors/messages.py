"""Message resolution - config rows are the single source of send copy.

Every kind's title/body live on its NotificationKindConfig row (per-language
columns via modeltranslation); rendering formats them with the notification's
context under the ACTIVE locale - callers set it first (delivery tasks use
translation.override(recipient.language); the API renders under the request
locale). Rendered text is never stored - that would freeze one language.

There is no code fallback: a missing row is a deploy error and raises
immediately. Two reads, one road each: ``notification_config_get`` is the
single-row query for a single-kind caller (a send resolving its channels, a
config edit); ``notification_render`` takes the map from
``notification_config_map`` - loaded ONCE per executor batch / API request,
never cached across requests (a live admin edit must be authoritative
immediately, and workers are separate processes).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig

ConfigMap = Mapping[NotificationKind, NotificationKindConfig]

_MISSING_ROW_HINT = (
    "seeded by `manage.py seed_notification_config` in the release step; a new "
    "NotificationKind needs its catalog entry in the same change "
    "(test_config enforces the pairing)"
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


def notification_config_get(*, kind: NotificationKind) -> NotificationKindConfig:
    """One kind's row, one query - for callers that have no batch to map."""
    try:
        return NotificationKindConfig.objects.get(kind=kind)
    except NotificationKindConfig.DoesNotExist:
        msg = (
            f"NotificationKindConfig row missing for {kind.name} ({_MISSING_ROW_HINT})."
        )
        raise LookupError(msg) from None


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    title: str
    body: str


def notification_render(
    *,
    kind: NotificationKind,
    context: Mapping[str, Any],
    configs: ConfigMap,
) -> RenderedMessage:
    """Render one message under the ACTIVE locale - callers set it first."""
    config = configs[kind]
    return RenderedMessage(
        title=config.title.format(**context),
        body=config.body.format(**context),
    )
