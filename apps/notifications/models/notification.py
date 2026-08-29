from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.notifications.validators import validate_kind


class Notification(BaseModel):
    """One in-app inbox row per recipient (per-recipient read state).

    Content is (kind, context) rendered from the catalog at send/read time
    in the viewer's locale - never pre-rendered text. Outbound state lives
    in NotificationDelivery rows (one per channel), NOT here: ``read_at``
    is the in-app fact (the user opened the inbox), a delivery's READ status
    is the provider's receipt - different facts, never synced.
    """

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("recipient"),
    )
    kind = models.CharField(_("kind"), max_length=50, validators=[validate_kind])
    context = models.JSONField(_("context"), default=dict, blank=True)
    read_at = models.DateTimeField(_("read at"), null=True, blank=True)
    broadcast = models.ForeignKey(
        "notifications.Broadcast",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name=_("broadcast"),
    )

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        indexes = [
            models.Index(fields=["recipient", "read_at"]),  # unread count/list
        ]
        constraints = [
            # Dispatch-resume backstop: re-running a page can never write a
            # second inbox row for the same (broadcast, recipient).
            models.UniqueConstraint(
                fields=["broadcast", "recipient"],
                condition=models.Q(broadcast__isnull=False),
                name="uniq_notification_broadcast_recipient",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind} for {self.recipient}"
