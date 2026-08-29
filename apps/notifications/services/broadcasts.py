"""Broadcast lifecycle: author a draft, dispatch it.

Dispatch is a guarded conditional UPDATE (DRAFT -> DISPATCHING; rowcount 0
= someone else already moved it) plus an on_commit enqueue of the
dispatcher task on the bulk queue - the request/admin transaction never
does fan-out work itself (ATOMIC_REQUESTS).

There is NO auto-retry by design: ``services.deliveries_resume`` is the
explicit recovery path (the admin "Resume incomplete" action), re-enqueuing
exactly the rows that are still PENDING (plus stale PROCESSING resets, plus
FAILED when asked). At-least-once in crash windows is accepted and documented.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.notifications.catalog import MessageTemplate
from apps.notifications.catalog import catalog_entry
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import BroadcastAudienceError
from apps.notifications.exceptions import BroadcastStateError
from apps.notifications.models import Broadcast
from apps.notifications.services.notifications import _validate_context
from apps.notifications.tasks import dispatch_broadcast
from apps.users.models import User


def notification_broadcast(
    *,
    kind: NotificationKind,
    context: dict[str, Any],
    language: str,
    require_device: bool,
    joined_after: date | None,
    joined_before: date | None,
    channels: Sequence[str],
    recipient_ids: Sequence[str],
    actor: User,
) -> Broadcast:
    """Author a DRAFT broadcast; nothing sends until broadcast_dispatch.

    ``channels`` is exactly what this send goes out on - every broadcast picks
    its own (a non-empty subset of what the kind supports; "send on nothing"
    is not a broadcast). There is no kind-level default behind it.
    ``recipient_ids`` hand-picks the audience: non-empty = exactly these
    users (the language/date filters are then ignored); empty = the filters.
    """
    entry = catalog_entry(kind)
    _validate_context(kind=kind, entry=entry, context=context)
    resolved_channels = _validate_channels(entry=entry, channels=channels)
    resolved_recipients = _validate_recipients(recipient_ids=recipient_ids)
    if joined_after and joined_before and joined_after > joined_before:
        raise BroadcastAudienceError(
            str(_("The joined-after date must not be later than joined-before."))
        )
    broadcast = Broadcast(
        kind=kind,
        context=context,
        language=language,
        require_device=require_device,
        joined_after=joined_after,
        joined_before=joined_before,
        channels=resolved_channels,
        recipient_ids=resolved_recipients,
        created_by=actor,
    )
    broadcast.full_clean()
    broadcast.save()
    return broadcast


def _validate_channels(*, entry: MessageTemplate, channels: Sequence[str]) -> list[str]:
    """A non-empty subset of the kind's supported channels.

    Unlike ``_validate_context`` (a programming error, no envelope) this one is
    operator input, so it raises an ApplicationError and surfaces as a message.
    """
    selected = set(map(str, channels))
    if not selected:
        raise BroadcastAudienceError(str(_("Pick at least one channel.")))
    supported = {str(channel) for channel in entry.supported_channels}
    unsupported = sorted(selected - supported)
    if unsupported:
        raise BroadcastAudienceError(
            str(
                _("This notification kind cannot be sent on: %(channels)s.")
                % {"channels": ", ".join(unsupported)}
            )
        )
    return sorted(selected)


def _validate_recipients(*, recipient_ids: Sequence[str]) -> list[str]:
    """Deduplicated, sorted pks of ACTIVE users - a stale pick (deleted or
    deactivated since the search) is operator-visible, not silently dropped."""
    wanted = sorted({str(pk) for pk in recipient_ids})
    if not wanted:
        return []
    found = {
        str(pk)
        for pk in User.objects.filter(is_active=True, pk__in=wanted).values_list(
            "pk", flat=True
        )
    }
    missing = [pk for pk in wanted if pk not in found]
    if missing:
        raise BroadcastAudienceError(
            str(
                _("%(count)d selected user(s) no longer exist or are inactive.")
                % {"count": len(missing)}
            )
        )
    return wanted


def broadcast_dispatch(*, broadcast: Broadcast) -> Broadcast:
    """DRAFT -> DISPATCHING (guarded) + dispatcher enqueue on commit."""
    updated = Broadcast.objects.filter(
        pk=broadcast.pk, status=BroadcastStatus.DRAFT
    ).update(status=BroadcastStatus.DISPATCHING, updated_at=timezone.now())
    if not updated:
        raise BroadcastStateError(str(_("Broadcast was already dispatched.")))
    transaction.on_commit(lambda: dispatch_broadcast.enqueue(str(broadcast.pk)))
    broadcast.refresh_from_db()
    return broadcast
