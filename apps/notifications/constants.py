from enum import StrEnum

from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationKind(models.TextChoices):
    """Every notification the product can send; each needs a catalog entry."""

    WELCOME = "welcome", _("Welcome")
    ANNOUNCEMENT = "announcement", _("Announcement")


class Channel(StrEnum):
    """Delivery channels BEYOND the in-app inbox row (which always exists)."""

    PUSH = "push"
    SMS = "sms"


class DevicePlatform(models.TextChoices):
    ANDROID = "android", _("Android")
    IOS = "ios", _("iOS")
    WEB = "web", _("Web")
