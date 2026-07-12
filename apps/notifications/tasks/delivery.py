"""Delivery workers: one task per channel, idempotent via *_sent_at markers.

A provider failure escapes and fails the task loudly (FAILED row + Sentry);
the markers make manual re-enqueue safe - already-delivered channels no-op.
Rendering happens HERE, under the recipient's language (no request in a
worker) - never at creation time.
"""

from django.tasks import task
from django.utils import timezone
from django.utils import translation


@task()
def send_push_notification(notification_id: str) -> None:
    from apps.notifications import selectors
    from apps.notifications.catalog import notification_render
    from apps.notifications.clients.push import push_send
    from apps.notifications.constants import NotificationKind
    from apps.notifications.models import Device
    from apps.notifications.models import Notification

    notification = Notification.objects.select_related("recipient").get(
        pk=notification_id
    )
    if notification.push_sent_at is not None:
        return
    recipient = notification.recipient
    tokens = selectors.device_tokens_for_user(user=recipient)
    if tokens:
        with translation.override(recipient.language):
            message = notification_render(
                kind=NotificationKind(notification.kind), context=notification.context
            )
        report = push_send(
            tokens=tokens,
            title=message.title,
            body=message.body,
            data={"notification_id": str(notification.pk), "kind": notification.kind},
        )
        if report.invalid_tokens:  # FCM says these tokens are dead - prune
            Device.objects.filter(registration_id__in=report.invalid_tokens).delete()
    notification.push_sent_at = timezone.now()
    notification.save(update_fields=["push_sent_at", "updated_at"])


@task()
def send_sms_notification(notification_id: str) -> None:
    from apps.notifications.catalog import notification_render
    from apps.notifications.clients.sms import sms_send
    from apps.notifications.constants import NotificationKind
    from apps.notifications.models import Notification

    notification = Notification.objects.select_related("recipient").get(
        pk=notification_id
    )
    if notification.sms_sent_at is not None:
        return
    recipient = notification.recipient
    if recipient.phone:  # phone-less users are skipped, not failed
        with translation.override(recipient.language):
            message = notification_render(
                kind=NotificationKind(notification.kind), context=notification.context
            )
        sms_send(to=str(recipient.phone), body=message.body)
    notification.sms_sent_at = timezone.now()
    notification.save(update_fields=["sms_sent_at", "updated_at"])
