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
    # Audience filters; every one unset = every active user. The queryset that
    # reads them lives in selectors/broadcasts.py - both the dispatcher's
    # paging and the composer's reach estimate go through it.
    language = models.CharField(
        _("language filter"), max_length=2, choices=Language, blank=True
    )
    require_device = models.BooleanField(
        _("registered device required"),
        default=False,
        help_text=_(
            "Skip users with no registered device. They would receive an inbox "
            "entry but no push."
        ),
    )
    joined_after = models.DateField(_("joined on or after"), null=True, blank=True)
    joined_before = models.DateField(_("joined on or before"), null=True, blank=True)
    # Empty = the kind's channel policy (its NotificationKindConfig row) at
    # dispatch time. A non-empty list overrides it for this send only, so one
    # announcement can add SMS without changing policy for every announcement.
    # ``services.notification_broadcast`` takes ``None`` for the former and
    # rejects an empty override - the column is the storage form, not the API.
    channels = models.JSONField(_("channels"), default=list, blank=True)
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
