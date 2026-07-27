from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind


class NotificationChannelOverride(BaseModel):
    """Operator toggle: pin one channel on/off for one kind, at runtime.

    Sparse rows, not per-kind boolean columns: a row pins ONLY the channel
    it names, every unpinned channel keeps following the catalog default -
    and a new channel needs no migration. Resolution lives in
    ``selectors.config.effective_channels``.
    """

    kind = models.CharField(_("kind"), max_length=50, choices=NotificationKind)
    channel = models.CharField(_("channel"), max_length=20, choices=Channel)
    enabled = models.BooleanField(_("enabled"))

    class Meta:
        verbose_name = _("channel override")
        verbose_name_plural = _("channel overrides")
        constraints = [
            models.UniqueConstraint(
                fields=["kind", "channel"], name="uniq_override_kind_channel"
            ),
        ]

    def __str__(self) -> str:
        state = "on" if self.enabled else "off"
        return f"{self.kind}/{self.channel} -> {state}"

    def clean(self) -> None:
        """Only channels the catalog supports for the kind may be pinned."""
        from apps.notifications.catalog import catalog_entry

        try:
            entry = catalog_entry(NotificationKind(self.kind))
            channel = Channel(self.channel)
        except ValueError, LookupError:
            return  # invalid choice - clean_fields already reports it
        if channel not in entry.supported_channels:
            supported = ", ".join(sorted(entry.supported_channels))
            raise ValidationError(
                {
                    "channel": _(
                        "%(kind)s does not support %(channel)s. "
                        "Supported channels: %(supported)s."
                    )
                    % {
                        "kind": self.kind,
                        "channel": self.channel,
                        "supported": supported,
                    }
                }
            )
