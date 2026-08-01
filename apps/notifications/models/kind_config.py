"""Per-kind runtime config: explicit channels + operator-editable copy."""

import string

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from modeltranslation.manager import MultilingualManager

from apps.common.models import BaseModel
from apps.notifications.constants import NotificationKind


def _placeholders(text: str) -> set[str]:
    """Field names a str.format call on ``text`` would look up."""
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}


class NotificationKindConfig(BaseModel):
    """One row per notification kind: its channels and its message, explicit.

    ``objects`` is declared as MultilingualManager up front: modeltranslation
    would swap a plain Manager's __class__ at registration time anyway, and
    the runtime-swapped class is one the django-stubs plugin cannot resolve
    (django-manager-missing otherwise). Declaring it keeps runtime and static
    views identical.

    There is no fallback layer - the row IS the policy (empty ``channels`` =
    inbox-only) and the copy source (``title``/``body`` have ar/en columns via
    modeltranslation; rendering resolves the active language). Rows are born
    in migration 0004, one per kind, and are never added or deleted through
    the admin; ``selectors.messages`` raises loudly when one is missing. The
    catalog keeps the code-side contract (context keys, supported channels,
    category, WhatsApp template) plus the seed values these rows start from.
    """

    objects = MultilingualManager["NotificationKindConfig"]()

    kind = models.CharField(
        _("action"), max_length=50, choices=NotificationKind, unique=True
    )
    # JSON list of Channel values; subset-of-supported enforced in clean().
    channels = models.JSONField(_("channels"), default=list, blank=True)
    title = models.CharField(_("title"), max_length=255)
    body = models.TextField(_("body"))

    # modeltranslation shadow columns (translation.py registers them); bare
    # annotations only - Django ignores them, mypy learns the attributes.
    title_ar: str | None
    title_en: str | None
    body_ar: str | None
    body_en: str | None

    class Meta:
        # Deterministic card order for the config screen and the admin gates.
        ordering = ["kind"]
        verbose_name = _("notification action")
        verbose_name_plural = _("notification actions")

    def __str__(self) -> str:
        channels = ", ".join(self.channels) if self.channels else "inbox only"
        return f"{self.kind} -> {channels}"

    def clean(self) -> None:
        """Channels ⊆ supported; every placeholder ⊆ the kind's context_keys.

        The placeholder check is also the safety boundary: names outside the
        contract (including attribute traversals like ``{name.__class__}``)
        never reach str.format at render time.
        """
        from apps.notifications.catalog import catalog_entry

        try:
            entry = catalog_entry(NotificationKind(self.kind))
        except ValueError, LookupError:
            return  # invalid choice - clean_fields already reports it
        errors: dict[str, str] = {}
        supported = {str(channel) for channel in entry.supported_channels}
        channel_list = self.channels if isinstance(self.channels, list) else None
        if channel_list is None:
            errors["channels"] = str(_("Must be a list of channel names."))
        else:
            unsupported = sorted(set(map(str, channel_list)) - supported)
            if unsupported:
                errors["channels"] = str(
                    _(
                        "%(kind)s does not support: %(channels)s. "
                        "Supported channels: %(supported)s."
                    )
                    % {
                        "kind": self.kind,
                        "channels": ", ".join(unsupported),
                        "supported": ", ".join(sorted(supported)),
                    }
                )
        for field in ("title_ar", "title_en", "body_ar", "body_en"):
            value = getattr(self, field, None) or ""
            if not value:
                continue  # blank enforcement lives on the field (translation.py)
            unknown = sorted(_placeholders(value) - entry.context_keys)
            if unknown:
                errors[field] = str(
                    _(
                        "Unknown placeholders: %(unknown)s. "
                        "This action provides: %(keys)s."
                    )
                    % {
                        "unknown": ", ".join(f"{{{name}}}" for name in unknown),
                        "keys": ", ".join(
                            f"{{{key}}}" for key in sorted(entry.context_keys)
                        ),
                    }
                )
        if errors:
            raise ValidationError(errors)
