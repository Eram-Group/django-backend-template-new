"""Field validators for the enum-valued columns.

Deliberately NOT ``choices=``: Django copies a field's choices into migration
state, so every new NotificationKind (or a relabelled Channel) would demand a
no-op AlterField migration. A validator is referenced by import path, so the
enum can grow freely; the value set stays enforced by ``full_clean`` exactly
as choices would have (services always full_clean before save).
"""

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.notifications.constants import Channel
from apps.notifications.constants import NotificationKind


def validate_kind(value: str) -> None:
    if value not in NotificationKind.values:
        raise ValidationError(
            _("%(value)s is not a notification kind."), params={"value": value}
        )


def validate_channel(value: str) -> None:
    if value not in Channel.values:
        raise ValidationError(_("%(value)s is not a channel."), params={"value": value})
