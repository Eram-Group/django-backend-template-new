"""Message resolution - config rows are the single source of send copy.

Every kind's title/body live on its NotificationKindConfig row (per-language
columns via modeltranslation); rendering formats them with the notification's
context under the ACTIVE locale - callers set it first (delivery tasks use
translation.override(recipient.language); the API renders under the request
locale). Rendered text is never stored - that would freeze one language.

A kind without a row still renders: title and body fall back to the kind's
label, so a send never fails on missing copy; a placeholder the row's
context lacks (a row written before the catalog changed) renders as its
literal ``{key}``, and a template that somehow escaped validation falls back
to the label - one bad row can never fail a delivery batch or 500 the
inbox. Two reads, one road each:
``notification_config_get`` is the single-row query for a config edit (loud
when missing - there is nothing to edit); ``notification_render`` takes the
map from ``notification_config_map`` - loaded ONCE per executor batch / API
request, never cached across requests (a live admin edit must be
authoritative immediately, and workers are separate processes).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import structlog

from apps.notifications.constants import NotificationKind
from apps.notifications.models import NotificationKindConfig

ConfigMap = Mapping[NotificationKind, NotificationKindConfig]

logger = structlog.get_logger(__name__)

_MISSING_ROW_HINT = (
    "opening the Notification actions page creates it with recommended values"
)


def notification_config_map() -> dict[NotificationKind, NotificationKindConfig]:
    """All existing config rows in ONE query - the batch/request-scoped copy
    source. A kind without a row is simply absent (``notification_render``
    falls back to its label); a row whose kind was removed from
    ``NotificationKind`` is ignored (logged once per load) instead of
    crashing every page and worker that loads the map - no data migration
    needed to retire a kind.
    """
    configs: dict[NotificationKind, NotificationKindConfig] = {}
    for row in NotificationKindConfig.objects.all():
        try:
            configs[NotificationKind(row.kind)] = row
        except ValueError:
            logger.warning("notification_kind_retired", kind=row.kind, config_id=row.pk)
    return configs


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
    """Render one message under the ACTIVE locale - callers set it first.

    No config row: the kind's label stands in for both title and body.
    """
    config = configs.get(kind)
    if config is None:
        label = str(kind.label)
        return RenderedMessage(title=label, body=label)
    return RenderedMessage(
        title=_render(config.title, context=context, fallback=kind),
        body=_render(config.body, context=context, fallback=kind),
    )


class _Context(dict[str, Any]):
    """A stale row's missing key renders as its literal placeholder."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _render(
    template: str, *, context: Mapping[str, Any], fallback: NotificationKind
) -> str:
    try:
        return template.format_map(_Context(context))
    except (ValueError, IndexError, AttributeError, KeyError) as exc:
        # Validation rejects every shape that can raise here; a row that
        # escaped it (a template edited outside the admin) must still send.
        logger.exception(
            "notification_template_unrenderable",
            kind=str(fallback),
            error=str(exc),
        )
        return str(fallback.label)
