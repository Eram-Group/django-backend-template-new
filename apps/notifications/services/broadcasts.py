"""Broadcast lifecycle: author a draft, dispatch it.

Dispatch is a guarded conditional UPDATE (DRAFT -> DISPATCHING; rowcount 0
= someone else already moved it) plus the dispatcher task's enqueue in the
same transaction (the queue is this database) - the request/admin path
never does fan-out work itself.

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
from apps.notifications.catalog import validate_context
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.exceptions import BroadcastAudienceError
from apps.notifications.exceptions import BroadcastStateError
from apps.notifications.models import Broadcast
from apps.notifications.selectors.broadcasts import broadcast_audience_summary
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
    recipients: Sequence[User],
    actor: User,
) -> Broadcast:
    """Author a DRAFT broadcast; nothing sends until broadcast_dispatch.

    ``channels`` is exactly what this send goes out on - every broadcast picks
    its own (a non-empty subset of what the kind supports; "send on nothing"
    is not a broadcast). There is no kind-level default behind it.
    ``recipients`` hand-picks the audience: non-empty = exactly these users
    (the language/date filters are then ignored); empty = the filters.
    """
    entry = catalog_entry(kind)
    validate_context(kind=kind, entry=entry, context=context)
    resolved_channels = _validate_channels(entry=entry, channels=channels)
    resolved_recipients = _validate_recipients(recipients=recipients)
    if joined_after and joined_before and joined_after > joined_before:
        raise BroadcastAudienceError(
            str(_("The joined-after date must not be later than joined-before."))
        )
    with transaction.atomic():
        broadcast = Broadcast(
            kind=kind,
            context=context,
            language=language,
            require_device=require_device,
            joined_after=joined_after,
            joined_before=joined_before,
            channels=resolved_channels,
            created_by=actor,
        )
        broadcast.full_clean()
        broadcast.save()
        broadcast.recipients.set(resolved_recipients)
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


def _validate_recipients(*, recipients: Sequence[User]) -> list[User]:
    """Deduplicated ACTIVE users - a stale pick (deleted or deactivated since
    the pick) is operator-visible, not silently dropped."""
    wanted = {user.pk: user for user in recipients}
    if not wanted:
        return []
    found = set(
        User.objects.filter(is_active=True, pk__in=wanted).values_list("pk", flat=True)
    )
    missing = [pk for pk in wanted if pk not in found]
    if missing:
        raise BroadcastAudienceError(
            str(
                _("%(count)d selected user(s) no longer exist or are inactive.")
                % {"count": len(missing)}
            )
        )
    return list(wanted.values())


def broadcast_dispatch(*, broadcast: Broadcast) -> Broadcast:
    """DRAFT -> DISPATCHING (guarded) + dispatcher enqueue, one transaction.

    An empty audience is refused up front: dispatching nothing would only
    mark the broadcast COMPLETED with zero deliveries, which reads as a
    successful send.
    """
    if broadcast_audience_summary(broadcast=broadcast)["recipients"] == 0:
        raise BroadcastAudienceError(
            str(_("This audience is empty - nothing would be sent."))
        )
    with transaction.atomic():
        updated = Broadcast.objects.filter(
            pk=broadcast.pk, status=BroadcastStatus.DRAFT
        ).update(status=BroadcastStatus.DISPATCHING, updated_at=timezone.now())
        if not updated:
            raise BroadcastStateError(str(_("Broadcast was already dispatched.")))
        dispatch_broadcast.enqueue(str(broadcast.pk))  # commits with the status
    broadcast.refresh_from_db()
    return broadcast
