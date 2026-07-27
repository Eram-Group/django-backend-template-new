from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.notifications.constants import BroadcastStatus
from apps.notifications.constants import NotificationKind
from apps.users.constants import Language


class Broadcast(BaseModel):
    """One mass send of a kind to an audience, fanned out in batches.

    Status moves DRAFT -> DISPATCHING -> DISPATCHED -> COMPLETED through
    guarded conditional UPDATEs (rowcount 0 = someone else already moved it),
    so a double-enqueued dispatcher can never run twice. ``dispatch_cursor``
    is the last user pk a dispatch page committed - rows and cursor land in
    one transaction, so a crashed dispatcher resumes exactly where it
    stopped. Counters are F()-updated by delivery batches; the admin
    "refresh progress" action recounts to fix any drift.
    """

    kind = models.CharField(_("kind"), max_length=50, choices=NotificationKind)
    context = models.JSONField(_("context"), default=dict, blank=True)
    status = models.CharField(
        _("status"),
        max_length=20,
        choices=BroadcastStatus,
        default=BroadcastStatus.DRAFT,
    )
    # Optional audience filter; blank = every active user.
    language = models.CharField(
        _("language filter"), max_length=2, choices=Language, blank=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="broadcasts_created",
        verbose_name=_("created by"),
    )
    dispatch_cursor = models.UUIDField(_("dispatch cursor"), null=True, blank=True)
    total_recipients = models.PositiveIntegerField(_("total recipients"), default=0)
    total_deliveries = models.PositiveIntegerField(_("total deliveries"), default=0)
    sent_count = models.PositiveIntegerField(_("sent"), default=0)
    failed_count = models.PositiveIntegerField(_("failed"), default=0)
    skipped_count = models.PositiveIntegerField(_("skipped"), default=0)

    class Meta:
        verbose_name = _("broadcast")
        verbose_name_plural = _("broadcasts")

    def __str__(self) -> str:
        return f"{self.kind} broadcast ({self.status})"
