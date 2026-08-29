from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.validators import validate_channel


class NotificationDelivery(BaseModel):
    """One outbound attempt of one notification on one channel.

    THE idempotency record: unique (notification, channel) means a re-run of
    any task can never double-send - the executor claims rows PENDING ->
    PROCESSING and skips everything else. ``broadcast`` denormalizes
    notification.broadcast so progress/resume queries stay index-only at
    100k rows. ``provider`` + ``provider_message_id`` key status webhooks
    (the correlation id - a different thing from the idempotency key).
    """

    notification = models.ForeignKey(
        "notifications.Notification",
        on_delete=models.CASCADE,
        related_name="deliveries",
        verbose_name=_("notification"),
    )
    broadcast = models.ForeignKey(
        "notifications.Broadcast",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="deliveries",
        verbose_name=_("broadcast"),
    )
    channel = models.CharField(
        _("channel"), max_length=20, validators=[validate_channel]
    )
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=DeliveryStatus,
        default=DeliveryStatus.PENDING,
    )
    provider = models.CharField(_("provider"), max_length=50, blank=True)
    provider_message_id = models.CharField(
        _("provider message id"), max_length=255, blank=True
    )
    detail = models.TextField(_("detail"), blank=True)
    sent_at = models.DateTimeField(_("sent at"), null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(_("attempts"), default=0)

    class Meta:
        verbose_name = _("notification delivery")
        verbose_name_plural = _("notification deliveries")
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "channel"],
                name="uniq_delivery_notification_channel",
            ),
            # Webhook lookups resolve one row per provider message id; blank
            # ids (push/SMS return none) stay out of the constraint.
            models.UniqueConstraint(
                fields=["provider", "provider_message_id"],
                condition=~models.Q(provider_message_id=""),
                name="uniq_delivery_provider_message_id",
            ),
        ]
        indexes = [
            models.Index(fields=["channel", "status"]),
            models.Index(
                fields=["broadcast", "status"],
                condition=models.Q(broadcast__isnull=False),
                name="idx_delivery_broadcast_status",
            ),
            # The transactional sweep: orphaned PENDING/PROCESSING rows with
            # no broadcast to resume from. (Index names cap at 30 chars.)
            models.Index(
                fields=["status", "created_at"],
                condition=models.Q(broadcast__isnull=True)
                & models.Q(status__in=["pending", "processing"]),
                name="idx_delivery_txn_sweep",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.channel} delivery ({self.status})"
