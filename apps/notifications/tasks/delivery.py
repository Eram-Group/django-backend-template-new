"""Delivery execution - the ONE pipeline single sends and broadcasts share.

``deliver_notifications`` takes a list of delivery pks (a single send passes
its 1-3 rows; broadcast batches pass ~200 channel-pure rows). The executor
claims PENDING rows atomically (select_for_update skip_locked -> PROCESSING),
so a double-enqueued or re-run task can never double-send - the unclaimed
rows are simply skipped.

Error policy (no auto-retry by design): a per-recipient provider rejection
marks that row FAILED and the task continues; a systemic failure
(*NotConfiguredError, OutboundTransportError/OutboundStatusError) escapes
and fails the task loudly (FAILED task row + Sentry) - claimed rows stay
PROCESSING and the sweep/resume path resets exactly that remainder.

Outcomes are persisted PER CHANNEL, in a ``finally``: a systemic failure in
the SMS step must never discard the push step's already-sent rows, or the
sweep would reset them to PENDING and the user would get the same push
again on every pass. Only rows the failing step had not reached stay
PROCESSING.

Rendering happens HERE, under each recipient's language (no request in a
worker) - never at creation time.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol

import structlog
from django.db import transaction
from django.db.models import F
from django.tasks import task
from django.utils import timezone
from django.utils import translation

from apps.notifications import selectors
from apps.notifications.catalog import catalog_entry
from apps.notifications.clients.push import PushMessage
from apps.notifications.clients.push import push_send_many
from apps.notifications.clients.sms import sms_send_many
from apps.notifications.clients.sms.base import SmsProviderError
from apps.notifications.clients.whatsapp import whatsapp_send_template
from apps.notifications.clients.whatsapp.base import WhatsAppProviderError
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.constants import NotificationKind
from apps.notifications.models import Broadcast
from apps.notifications.models import Device
from apps.notifications.models import NotificationDelivery

logger = structlog.get_logger(__name__)

WHATSAPP_PROVIDER = "whatsapp"


@task()
def deliver_notifications(delivery_ids: list[str]) -> None:
    execute_deliveries(delivery_ids=delivery_ids)


def execute_deliveries(*, delivery_ids: list[str]) -> None:
    """Claim, send per channel, record outcomes, bump broadcast progress."""
    claimed_ids = _claim(delivery_ids=delivery_ids)
    if not claimed_ids:
        return
    # Channel order is deterministic (push, sms, whatsapp): which channel a
    # systemic failure interrupts must not depend on row order in the DB.
    rows = list(
        NotificationDelivery.objects.filter(pk__in=claimed_ids)
        .select_related("notification__recipient")
        .order_by("channel", "pk")
    )
    by_channel: dict[str, list[NotificationDelivery]] = defaultdict(list)
    retired: list[NotificationDelivery] = []
    for row in rows:
        if row.notification.kind not in NotificationKind.values:
            # The kind left the enum after this row was queued: nothing can
            # render it. Terminal, so the sweep does not retry it forever.
            row.status = DeliveryStatus.SKIPPED
            row.detail = "kind retired"
            retired.append(row)
            continue
        by_channel[row.channel].append(row)
    if retired:
        _record_outcomes(retired)
    # One config query for the whole batch - rendering per row must not be.
    configs = selectors.notification_config_map()
    for channel, channel_rows in by_channel.items():
        try:
            _deliver_channel(channel, channel_rows, configs=configs)
        finally:
            _record_outcomes(channel_rows)


def _deliver_channel(
    channel: str, rows: list[NotificationDelivery], *, configs: selectors.ConfigMap
) -> None:
    """Exhaustive over ``Channel``: a value outside the enum (ValueError) or
    a member without a deliverer (KeyError) fails the task loudly - rows are
    only ever created for enum channels, so either is a code bug."""
    _DELIVERERS[Channel(channel)](rows, configs=configs)


def _claim(*, delivery_ids: list[str]) -> list[uuid.UUID]:
    """PENDING -> PROCESSING for exactly the rows THIS call now owns."""
    now = timezone.now()
    with transaction.atomic():
        claimed = list(
            NotificationDelivery.objects.select_for_update(skip_locked=True)
            .filter(pk__in=delivery_ids, status=DeliveryStatus.PENDING)
            .values_list("pk", flat=True)
        )
        if claimed:
            NotificationDelivery.objects.filter(pk__in=claimed).update(
                status=DeliveryStatus.PROCESSING,
                attempts=F("attempts") + 1,
                updated_at=now,
            )
    return claimed


def _deliver_push(
    rows: list[NotificationDelivery], *, configs: selectors.ConfigMap
) -> None:
    tokens_by_user = selectors.device_tokens_by_user_id(
        user_ids={row.notification.recipient_id for row in rows}
    )
    now = timezone.now()
    messages: list[PushMessage] = []
    owners: list[NotificationDelivery] = []
    for row in rows:
        recipient = row.notification.recipient
        if recipient.pk not in tokens_by_user:
            row.status = DeliveryStatus.SKIPPED
            row.detail = "no devices"
            continue
        tokens = tokens_by_user[recipient.pk]
        with translation.override(recipient.language):
            message = selectors.notification_render(
                kind=NotificationKind(row.notification.kind),
                context=row.notification.context,
                configs=configs,
            )
        data = {
            "notification_id": str(row.notification_id),
            "kind": row.notification.kind,
        }
        for token_value in tokens:
            messages.append(
                PushMessage(
                    token=token_value,
                    title=message.title,
                    body=message.body,
                    data=data,
                )
            )
            owners.append(row)
    if not messages:
        return
    results = push_send_many(messages=messages)
    delivered: set[uuid.UUID] = set()
    failure_detail: dict[uuid.UUID, str] = {}
    invalid_tokens: list[str] = []
    for owner, result in zip(owners, results, strict=True):
        if result.ok:
            delivered.add(owner.pk)
        else:
            failure_detail.setdefault(owner.pk, result.detail)
            if result.invalid:
                invalid_tokens.append(result.token)
    for row in rows:
        if row.status == DeliveryStatus.SKIPPED:
            continue
        if row.pk in delivered:  # at least one device got it
            row.status = DeliveryStatus.SENT
            row.sent_at = now
        else:  # every token failed - the first failure names why
            row.status = DeliveryStatus.FAILED
            row.detail = failure_detail.get(row.pk, "no result from push provider")
    if invalid_tokens:  # FCM says these tokens are dead - prune
        Device.objects.filter(registration_id__in=invalid_tokens).delete()


def _deliver_sms(
    rows: list[NotificationDelivery], *, configs: selectors.ConfigMap
) -> None:
    """One provider call per rendered-body group (language + kind + context).

    Bulk providers report counts, not per-number outcomes, so failure
    granularity is the GROUP - except for the numbers the backend reports
    as already accepted (``SmsProviderError.sent``: SMSMisr posts one number
    at a time, and the routing backend runs one provider after another).
    Those rows are SENT; only the rest fail, so a resume/sweep
    ``--include-failed`` can never re-bill a number that went through.
    """
    now = timezone.now()
    groups: dict[
        tuple[str, str, tuple[tuple[str, str], ...]], list[NotificationDelivery]
    ] = defaultdict(list)
    for row in rows:
        recipient = row.notification.recipient
        if not recipient.phone:
            row.status = DeliveryStatus.SKIPPED
            row.detail = "no phone"
            continue
        key = (
            recipient.language,
            row.notification.kind,
            tuple(sorted((k, str(v)) for k, v in row.notification.context.items())),
        )
        groups[key].append(row)
    for (language, kind, _context_key), grouped in groups.items():
        with translation.override(language):
            body = selectors.notification_render(
                kind=NotificationKind(kind),
                context=grouped[0].notification.context,
                configs=configs,
            ).body
        numbers = [str(r.notification.recipient.phone) for r in grouped]
        try:
            sms_send_many(to=numbers, body=body)
        except SmsProviderError as exc:
            accepted = set(exc.sent)
            for row, number in zip(grouped, numbers, strict=True):
                if number in accepted:
                    row.status = DeliveryStatus.SENT
                    row.sent_at = now
                else:
                    row.status = DeliveryStatus.FAILED
                    row.detail = str(exc)
        else:
            for row in grouped:
                row.status = DeliveryStatus.SENT
                row.sent_at = now


def _deliver_whatsapp(
    rows: list[NotificationDelivery], *, configs: selectors.ConfigMap
) -> None:
    """Meta hosts the per-language bodies: the send carries the template
    NAME, the recipient's language code and the ordered variables - the
    config-row copy (``configs``) is not rendered for this channel."""
    del configs
    now = timezone.now()
    for row in rows:
        recipient = row.notification.recipient
        if not recipient.phone:
            row.status = DeliveryStatus.SKIPPED
            row.detail = "no phone"
            continue
        entry = catalog_entry(NotificationKind(row.notification.kind))
        template = entry.whatsapp_template
        variables = [str(row.notification.context[key]) for key in template.variables]
        try:
            result = whatsapp_send_template(
                to=str(recipient.phone),
                template_name=template.name,
                language=recipient.language,
                variables=variables,
            )
        except WhatsAppProviderError as exc:
            row.status = DeliveryStatus.FAILED
            row.detail = exc.detail
        else:
            row.status = DeliveryStatus.SENT
            row.sent_at = now
            row.provider = WHATSAPP_PROVIDER
            row.provider_message_id = result.message_id


class _Deliverer(Protocol):
    def __call__(
        self, rows: list[NotificationDelivery], *, configs: selectors.ConfigMap
    ) -> None: ...


_DELIVERERS: dict[Channel, _Deliverer] = {
    Channel.PUSH: _deliver_push,
    Channel.SMS: _deliver_sms,
    Channel.WHATSAPP: _deliver_whatsapp,
}


def _record_outcomes(rows: list[NotificationDelivery]) -> None:
    """One bulk write per channel group, then broadcast progress + completion.

    Rows a failing step never reached are written back unchanged
    (PROCESSING) - they are not counted, and the broadcast cannot complete
    while they exist; the sweep resets exactly those.
    """
    now = timezone.now()
    for row in rows:
        row.updated_at = now  # bulk_update bypasses auto_now
    NotificationDelivery.objects.bulk_update(
        rows,
        [
            "status",
            "detail",
            "sent_at",
            "provider",
            "provider_message_id",
            "updated_at",
        ],
        batch_size=500,
    )
    per_broadcast: dict[uuid.UUID, dict[str, int]] = defaultdict(
        lambda: {"sent": 0, "failed": 0, "skipped": 0}
    )
    for row in rows:
        if row.broadcast_id is None:
            continue
        if row.status == DeliveryStatus.SENT:
            per_broadcast[row.broadcast_id]["sent"] += 1
        elif row.status == DeliveryStatus.FAILED:
            per_broadcast[row.broadcast_id]["failed"] += 1
        elif row.status == DeliveryStatus.SKIPPED:
            per_broadcast[row.broadcast_id]["skipped"] += 1
    for broadcast_id, counts in per_broadcast.items():
        Broadcast.objects.filter(pk=broadcast_id).update(
            sent_count=F("sent_count") + counts["sent"],
            failed_count=F("failed_count") + counts["failed"],
            skipped_count=F("skipped_count") + counts["skipped"],
            updated_at=now,
        )
        maybe_complete_broadcast(broadcast_id=broadcast_id)


def maybe_complete_broadcast(*, broadcast_id: uuid.UUID | str) -> None:
    """DISPATCHED -> COMPLETED once no delivery is pending or in flight."""
    Broadcast.objects.filter(
        pk=broadcast_id, status=BroadcastStatus.DISPATCHED
    ).exclude(
        deliveries__status__in=[DeliveryStatus.PENDING, DeliveryStatus.PROCESSING]
    ).update(status=BroadcastStatus.COMPLETED, updated_at=timezone.now())


def chunk_ids[T](ids: list[T], *, size: int) -> Iterable[list[T]]:
    """Slice a pk list into enqueue-sized chunks (shared with resume/sweep)."""
    for start in range(0, len(ids), size):
        yield ids[start : start + size]
