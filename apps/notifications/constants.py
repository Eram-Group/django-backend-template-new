from collections.abc import Sequence

from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext import StrOrPromise

#: FCM registration tokens are ~160 chars; the column and the API bound agree.
REGISTRATION_ID_MAX_LENGTH = 512


class NotificationKind(models.TextChoices):
    """Every notification the product can send; each needs a catalog entry."""

    WELCOME = "welcome", _("Welcome")
    ANNOUNCEMENT = "announcement", _("Announcement")
    PAYMENT_PAID = "payment_paid", _("Payment received")
    WALLET_CREDITED = "wallet_credited", _("Wallet credited")


def notification_kind_choices() -> Sequence[tuple[str, StrOrPromise]]:
    """``choices=`` for kind columns. A CALLABLE is serialized into migration
    state by import path, so adding a kind is never a migration - the value
    set is still enforced by ``full_clean`` and rendered by the admin."""
    return NotificationKind.choices


class NotificationCategory(models.TextChoices):
    """MARKETING is the opt-out-able class of notification; TRANSACTIONAL is
    always delivered. Per-user preferences are not implemented - today the
    split only labels each NotificationKind in the catalog.
    """

    TRANSACTIONAL = "transactional", _("Transactional")
    MARKETING = "marketing", _("Marketing")


class Channel(models.TextChoices):
    """Delivery channels BEYOND the in-app inbox row (which always exists)."""

    PUSH = "push", _("App notification")
    SMS = "sms", _("SMS")
    WHATSAPP = "whatsapp", _("WhatsApp")


def channel_choices() -> Sequence[tuple[str, StrOrPromise]]:
    """``choices=`` for channel columns - see ``notification_kind_choices``."""
    return Channel.choices


class DeliveryStatus(models.TextChoices):
    """Lifecycle of one (notification, channel) delivery attempt.

    PENDING -> PROCESSING -> SENT -> DELIVERED -> READ move strictly forward
    (provider webhooks may skip steps); FAILED is reachable only from at
    most SENT; SKIPPED is terminal (no capability, or opted out). The
    monotonic guard lives in ``delivery_update_status``.
    """

    PENDING = "pending", _("Pending")
    PROCESSING = "processing", _("Processing")
    SENT = "sent", _("Sent")
    DELIVERED = "delivered", _("Delivered")
    READ = "read", _("Read")
    FAILED = "failed", _("Failed")
    SKIPPED = "skipped", _("Skipped")


class BroadcastStatus(models.TextChoices):
    """DRAFT -> DISPATCHING -> DISPATCHED -> COMPLETED, via guarded UPDATEs."""

    DRAFT = "draft", _("Draft")
    DISPATCHING = "dispatching", _("Dispatching")
    DISPATCHED = "dispatched", _("Dispatched")
    COMPLETED = "completed", _("Completed")


class DevicePlatform(models.TextChoices):
    ANDROID = "android", _("Android")
    IOS = "ios", _("iOS")
    WEB = "web", _("Web")
