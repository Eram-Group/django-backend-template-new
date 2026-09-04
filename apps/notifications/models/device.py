from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.notifications.constants import REGISTRATION_ID_MAX_LENGTH
from apps.notifications.constants import DevicePlatform


class Device(BaseModel):
    """One push-capable installation of a client app.

    Deliberately no is_active flag: unregister (logout) deletes the row, an
    FCM "unregistered" response deletes it, and the next app launch simply
    re-registers. updated_at doubles as last-seen.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="devices",
        verbose_name=_("user"),
    )
    # FCM tokens are ~160 chars; a bounded column keeps the unique index well
    # under Postgres's btree row limit (an unbounded text key can 500 on a
    # malformed registration).
    registration_id = models.CharField(
        _("registration id"), max_length=REGISTRATION_ID_MAX_LENGTH, unique=True
    )
    platform = models.CharField(_("platform"), max_length=10, choices=DevicePlatform)

    class Meta:
        verbose_name = _("device")
        verbose_name_plural = _("devices")

    def __str__(self) -> str:
        return f"{self.platform} device of {self.user}"
