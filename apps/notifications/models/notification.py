from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.notifications.constants import NotificationKind


class Notification(BaseModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
        verbose_name=_("recipient"),
    )
    kind = models.CharField(_("kind"), max_length=50, choices=NotificationKind)
    context = models.JSONField(_("context"), default=dict, blank=True)
    read_at = models.DateTimeField(_("read at"), null=True, blank=True)
    push_sent_at = models.DateTimeField(_("push sent at"), null=True, blank=True)
    sms_sent_at = models.DateTimeField(_("SMS sent at"), null=True, blank=True)

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        indexes = [
            models.Index(fields=["recipient", "read_at"]),  # unread count/list
        ]

    def __str__(self) -> str:
        return f"{self.kind} for {self.recipient}"
