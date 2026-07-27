from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
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
    registration_id = models.TextField(_("registration id"), unique=True)
    platform = models.CharField(_("platform"), max_length=10, choices=DevicePlatform)

    class Meta:
        verbose_name = _("device")
        verbose_name_plural = _("devices")

    def __str__(self) -> str:
        return f"{self.platform} device of {self.user}"
